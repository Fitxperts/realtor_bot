"""WhatsApp (Cloud API): входящие сообщения агентству → заявка админам в Telegram.

Подготовка к WhatsApp как второму каналу. Сейчас:
- вебхук агентства: GET/POST /whatsapp/a<id> — проверка (hub.mode/hub.verify_token/
  hub.challenge) и подпись каждого POST (X-Hub-Signature-256 = HMAC-SHA256 тела
  по App Secret);
- каждое входящее сообщение — админам агентства в Telegram: имя, номер, текст и
  кнопка «Ответить в WhatsApp» (wa.me); событие «wa_lead» в аналитике;
- автоответ клиенту (не чаще раза в сутки на номер): «приняли, агент ответит» +
  ссылка на каталог в Telegram-боте агентства;
- повторы доставки от Meta (тот же id сообщения) не дублируются.

Каталог и анкеты прямо в WhatsApp — следующий шаг (клиенты в CRM пока
привязаны к Telegram). Настраивает владелец платформы: /whatsapp в боте платформы.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from html import escape

import aiohttp
from aiohttp import web

from bot import crypto
from bot.database.models import Agency

logger = logging.getLogger(__name__)

REPLY_EVERY = 24 * 3600            # автоответ одному номеру — не чаще раза в сутки
_seen: OrderedDict[str, None] = OrderedDict()      # id уже обработанных сообщений (повторы Meta)
_replied: dict[tuple[int, str], float] = {}         # (агентство, номер) → когда отвечали


def api_version() -> str:
    return os.getenv("WHATSAPP_API_VERSION", "").strip() or "v23.0"


@dataclass(frozen=True)
class Incoming:
    message_id: str
    sender: str            # wa_id (номер в международном формате без «+»), если Meta его прислала
    name: str
    text: str


@dataclass(frozen=True)
class Credentials:
    phone_id: str
    token: str
    app_secret: str
    verify_token: str


def credentials(row: Agency | None) -> Credentials | None:
    if row is None or not (row.wa_phone_id and row.wa_token_enc and row.wa_secret_enc and row.wa_verify_token):
        return None
    try:
        return Credentials(row.wa_phone_id, crypto.decrypt(row.wa_token_enc), crypto.decrypt(row.wa_secret_enc),
                           row.wa_verify_token)
    except Exception:  # noqa: BLE001 - ключ шифрования сменили / не задан
        logger.exception("WhatsApp агентства %s: не расшифровать ключи", row.id)
        return None


def configure(row: Agency, phone_id: str, token: str, app_secret: str) -> str:
    """Сохранить ключи (зашифрованы) и выдать новый verify token для кабинета Meta."""
    row.wa_phone_id = phone_id
    row.wa_token_enc = crypto.encrypt(token)
    row.wa_secret_enc = crypto.encrypt(app_secret)
    row.wa_verify_token = secrets.token_urlsafe(24)
    return row.wa_verify_token


def disable(row: Agency) -> None:
    row.wa_phone_id = row.wa_token_enc = row.wa_secret_enc = row.wa_verify_token = None


def signature_ok(body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse(payload: dict, phone_id: str) -> list[Incoming]:
    """Входящие сообщения из уведомления Meta (только для номера этого агентства)."""
    result = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if str((value.get("metadata") or {}).get("phone_number_id", "")) != phone_id:
                continue
            contacts = value.get("contacts") or [{}]
            for msg in value.get("messages") or []:
                contact = next((c for c in contacts if c.get("wa_id") == msg.get("from")), contacts[0])
                sender = str(contact.get("wa_id") or msg.get("from") or "")
                kind = msg.get("type", "")
                if kind == "text":
                    text = (msg.get("text") or {}).get("body", "")
                elif kind in ("image", "video", "document", "audio"):
                    text = f"[{kind}] " + ((msg.get(kind) or {}).get("caption") or "")
                else:
                    text = f"[{kind}]"
                result.append(Incoming(str(msg.get("id", "")), sender if sender.isdigit() else "",
                                       str((contact.get("profile") or {}).get("name") or ""), text.strip()))
    return result


def _first_time(message_id: str) -> bool:
    if not message_id or message_id in _seen:
        return not message_id
    _seen[message_id] = None
    while len(_seen) > 5000:
        _seen.popitem(last=False)
    return True


async def send_text(creds: Credentials, to: str, text: str) -> bool:
    """POST /{phone_number_id}/messages — свободный текст (в течение 24 ч после сообщения клиента)."""
    url = f"https://graph.facebook.com/{api_version()}/{creds.phone_id}/messages"
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text",
               "text": {"preview_url": True, "body": text}}
    try:
        async with aiohttp.ClientSession() as http, http.post(
                url, json=payload, headers={"Authorization": f"Bearer {creds.token}"},
                timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status >= 400:
                logger.warning("WhatsApp: ответ не отправлен (%s): %s", resp.status, (await resp.text())[:300])
                return False
            return True
    except Exception:  # noqa: BLE001
        logger.exception("WhatsApp: ответ не отправлен")
        return False


def _reply_text(agency_name: str, bot_username: str) -> str:
    link = f"\nKatalog / Каталог: https://t.me/{bot_username}" if bot_username else ""
    return (f"Assalomu alaykum! {agency_name}: xabaringiz qabul qilindi, agent tez orada javob beradi.\n"
            f"Здравствуйте! {agency_name}: сообщение получено, агент скоро ответит.{link}")


async def handle(agency_id: int, row: Agency, items: list[Incoming], creds: Credentials) -> int:
    """Уведомить админов агентства и (раз в сутки) ответить клиенту. Возвращает число новых сообщений."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from bot import bots
    from bot.agency_config import cfg
    from bot.services import analytics
    from bot.services.notify import notify_admins
    from bot.tenancy import agency_scope

    fresh = [m for m in items if _first_time(m.message_id)]
    if not fresh:
        return 0
    agency_bot = bots.bot_for(agency_id)
    with agency_scope(agency_id):
        config = cfg()
        for msg in fresh:
            who = escape(msg.name or "—") + (f" · +{msg.sender}" if msg.sender else "")
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="💬 Ответить в WhatsApp", url=f"https://wa.me/{msg.sender}")]]) if msg.sender else None
            if agency_bot is not None:
                await notify_admins(agency_bot, f"💬 <b>WhatsApp</b>: {who}\n{escape(msg.text[:1500]) or '—'}",
                                    reply_markup=kb)
            await analytics.track("wa_lead")
            key = (agency_id, msg.sender)
            if msg.sender and time.time() - _replied.get(key, 0) > REPLY_EVERY:
                _replied[key] = time.time()
                await send_text(creds, msg.sender, _reply_text(config.name, config.bot_username))
    return len(fresh)


def _agency_id(request: web.Request) -> int | None:
    match = re.fullmatch(r"a(\d{1,12})", request.match_info.get("agency", ""))
    return int(match.group(1)) if match else None


def register_routes(app: web.Application, maker_factory) -> None:
    async def load(request: web.Request) -> tuple[int, Agency, Credentials] | None:
        agency_id = _agency_id(request)
        if agency_id is None:
            return None
        async with maker_factory()() as session:
            row = await session.get(Agency, agency_id)
        creds = credentials(row)
        if creds is None or row.status == "disabled":
            return None
        return agency_id, row, creds

    async def verify(request: web.Request) -> web.Response:
        loaded = await load(request)
        q = request.query
        if loaded and q.get("hub.mode") == "subscribe" and \
                hmac.compare_digest(q.get("hub.verify_token", ""), loaded[2].verify_token):
            return web.Response(text=q.get("hub.challenge", ""))
        return web.Response(status=403)

    async def receive(request: web.Request) -> web.Response:
        body = await request.read()
        loaded = await load(request)
        if loaded is None:
            return web.Response(status=404)
        agency_id, row, creds = loaded
        if not signature_ok(body, request.headers.get("X-Hub-Signature-256"), creds.app_secret):
            logger.warning("WhatsApp агентства %s: неверная подпись", agency_id)
            return web.Response(status=401)
        try:
            items = parse(json.loads(body), creds.phone_id)
            await handle(agency_id, row, items, creds)
        except Exception:  # noqa: BLE001 - Meta повторяет доставку при не-200; ошибку только логируем
            logger.exception("WhatsApp агентства %s: сбой обработки", agency_id)
        return web.Response(text="ok")

    app.router.add_get("/whatsapp/{agency}", verify)
    app.router.add_post("/whatsapp/{agency}", receive)
