"""Онбординг агентства: название → город → токен бота → канал → контакты → запуск.

Токен проверяется у Telegram (getMe), сообщение с ним сразу удаляется; права бота
в канале проверяются (getChatMember). После «Запустить» агентство создаётся в БД
(токен зашифрован), бот подключается на ходу — без перезапуска сервера.
"""
from __future__ import annotations

import logging
import re
import secrets
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bots, crypto
from bot.agency_config import from_row, register
from bot.config import get_settings
from bot.database.models import Agency
from bot.platform.texts import CITIES, t
from bot.states.platform_states import Onboarding
from bot.tenancy import DEFAULT_AGENCY_ID

logger = logging.getLogger(__name__)
router = Router(name="platform_onboarding")

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
CHANNEL_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")
MAX_AGENCIES_PER_OWNER = 3
# Создание временного бота для проверки токена/канала (в тестах подменяется)
bot_factory = bots.make_bot


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def _lang(state: FSMContext) -> str:
    return (await state.get_data()).get("lang", "ru")


# ---------------------------------------------------------------------------
# Проверки (отдельно — их удобно тестировать)
# ---------------------------------------------------------------------------
async def check_token(token: str) -> tuple[int, str]:
    """(bot_id, username) или ValueError, если Telegram токен не принял."""
    temp = bot_factory(token)
    try:
        me = await temp.get_me()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(str(exc)) from exc
    finally:
        await temp.session.close()
    return me.id, me.username or ""


async def check_channel(token: str, bot_id: int, channel: str) -> bool:
    """Бот — администратор канала с правом публикации?"""
    temp = bot_factory(token)
    try:
        member = await temp.get_chat_member(channel, bot_id)
    except Exception:  # noqa: BLE001 - канала нет / бот не участник
        return False
    finally:
        await temp.session.close()
    if member.status == ChatMemberStatus.CREATOR:
        return True
    return member.status == ChatMemberStatus.ADMINISTRATOR and bool(getattr(member, "can_post_messages", False))


def parse_contacts(text: str) -> str:
    """«+998901234567 @agent» (по строке на человека) → «+998901234567:agent,…»."""
    pairs = []
    for line in text.splitlines():
        phone = re.search(r"\+?\d[\d\s\-()]{7,}\d", line)
        user = re.search(r"@([A-Za-z0-9_]{4,32})", line)
        if phone:
            digits = re.sub(r"[^\d+]", "", phone.group())
            pairs.append(f"{digits}:{user.group(1) if user else ''}")
    return ",".join(pairs)[:255]


async def create_agency(session: AsyncSession, data: dict, owner_id: int) -> Agency:
    """Агентство в БД (токен зашифрован) + бот подключается сразу."""
    secret = secrets.token_urlsafe(24)
    channel = data["channel"]
    row = Agency(
        name=data["name"][:128], city=data["city"][:64], status="active",
        bot_token_enc=crypto.encrypt(data["token"]), bot_id=data["bot_id"], bot_username=data["username"],
        webhook_secret=secret, channel_id=channel, channel_url=f"https://t.me/{channel[1:]}",
        contacts=data.get("contacts") or None, watermark=data["name"][:64], admin_ids=str(owner_id),
        owner_telegram_id=owner_id, language=data.get("lang", "uz"),
    )
    session.add(row)
    await session.commit()
    if row.id == DEFAULT_AGENCY_ID:  # №1 — основное агентство из .env; без его строки в БД — ошибка установки
        await session.delete(row)
        await session.commit()
        raise RuntimeError("В БД нет агентства №1 — не применена миграция 0006")
    register(from_row(row))
    await bots.activate(row.id, data["token"], secret)
    logger.info("Подключено агентство %s «%s» (@%s)", row.id, row.name, row.bot_username)
    return row


# ---------------------------------------------------------------------------
# Сценарий
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🌐 Tilni tanlang / Выберите язык:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        _btn("🇺🇿 O‘zbekcha", "pl:lang:uz"), _btn("🇷🇺 Русский", "pl:lang:ru"),
    ]]))


@router.callback_query(F.data.startswith("pl:lang:"))
async def choose_lang(callback: CallbackQuery, state: FSMContext) -> None:
    lang = callback.data.split(":")[2]
    await state.update_data(lang=lang)
    await callback.answer()
    await callback.message.answer(t("welcome", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        _btn(t("btn_connect", lang), "pl:connect"),
    ]]))


@router.callback_query(F.data == "pl:connect")
async def connect(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    lang = await _lang(state)
    await callback.answer()
    owned = await session.scalar(select(func.count()).select_from(Agency)
                                 .where(Agency.owner_telegram_id == callback.from_user.id))
    if owned >= MAX_AGENCIES_PER_OWNER and not get_settings().is_admin(callback.from_user.id):
        await callback.message.answer(t("limit", lang, n=owned))
        return
    await state.set_state(Onboarding.name)
    await callback.message.answer(t("ask_name", lang))


@router.message(Onboarding.name, F.text)
async def got_name(message: Message, state: FSMContext) -> None:
    lang = await _lang(state)
    await state.update_data(name=message.text.strip())
    await state.set_state(Onboarding.city)
    rows = [[_btn(c, f"pl:city:{i}") for i, c in enumerate(CITIES[j:j + 2], start=j)] for j in range(0, len(CITIES), 2)]
    await message.answer(t("ask_city", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(Onboarding.city, F.data.startswith("pl:city:"))
async def got_city_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(city=CITIES[int(callback.data.split(":")[2])])
    await _ask_token(callback.message, state)


@router.message(Onboarding.city, F.text)
async def got_city_text(message: Message, state: FSMContext) -> None:
    await state.update_data(city=message.text.strip()[:64])
    await _ask_token(message, state)


async def _ask_token(message: Message, state: FSMContext) -> None:
    await state.set_state(Onboarding.token)
    await message.answer(t("ask_token", await _lang(state)))


@router.message(Onboarding.token, F.text)
async def got_token(message: Message, state: FSMContext, session: AsyncSession) -> None:
    lang = await _lang(state)
    token = message.text.strip()
    try:
        await message.delete()  # токен = управление ботом; в переписке не оставляем
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось удалить сообщение с токеном")
    if not TOKEN_RE.match(token):
        await message.answer(t("token_bad", lang))
        return
    try:
        bot_id, username = await check_token(token)
    except ValueError:
        await message.answer(t("token_invalid", lang))
        return
    if await session.scalar(select(Agency.id).where(Agency.bot_id == bot_id)) is not None:
        await message.answer(t("token_taken", lang))
        return
    await state.update_data(token=token, bot_id=bot_id, username=username)
    await state.set_state(Onboarding.channel)
    await message.answer(t("token_ok", lang, username=username) + "\n\n" + t("ask_channel", lang))


@router.message(Onboarding.channel, F.text)
async def got_channel(message: Message, state: FSMContext) -> None:
    lang = await _lang(state)
    data = await state.get_data()
    match = CHANNEL_RE.match(message.text.strip().rsplit("/", 1)[-1])
    channel = f"@{match.group(1)}" if match else ""
    if not channel or not await check_channel(data["token"], data["bot_id"], channel):
        await message.answer(t("channel_bad", lang, username=data["username"]))
        return
    await state.update_data(channel=channel)
    await state.set_state(Onboarding.contacts)
    await message.answer(t("ask_contacts", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        _btn(t("btn_skip", lang), "pl:skipcontacts"),
    ]]))


@router.message(Onboarding.contacts, F.text)
async def got_contacts(message: Message, state: FSMContext) -> None:
    await state.update_data(contacts=parse_contacts(message.text))
    await _confirm(message, state)


@router.callback_query(Onboarding.contacts, F.data == "pl:skipcontacts")
async def skip_contacts(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(contacts="")
    await _confirm(callback.message, state)


async def _confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await state.set_state(Onboarding.confirm)
    contacts = ", ".join(p.replace(":", " @").rstrip(" @") for p in (data.get("contacts") or "").split(",") if p)
    await message.answer(
        t("confirm", lang, name=escape(data["name"]), city=escape(data["city"]), username=data["username"],
          channel=data["channel"], contacts=escape(contacts) or "—"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            _btn(t("btn_launch", lang), "pl:launch"), _btn(t("btn_cancel", lang), "pl:cancel"),
        ]]),
    )


@router.callback_query(Onboarding.confirm, F.data == "pl:cancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    lang = await _lang(state)
    await state.clear()
    await callback.answer()
    await callback.message.answer(t("cancelled", lang))


@router.callback_query(Onboarding.confirm, F.data == "pl:launch")
async def launch(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await callback.answer()
    try:
        row = await create_agency(session, data, callback.from_user.id)
    except crypto.SecretKeyMissing:
        logger.error("PLATFORM_SECRET_KEY не задан — онбординг невозможен")
        await callback.message.answer(t("unavailable", lang))
        return
    await state.clear()
    await callback.message.answer(t("done", lang, username=row.bot_username, channel=row.channel_id))
    who = escape(callback.from_user.full_name or "")
    for admin_id in get_settings().admin_ids:
        try:
            await bot.send_message(admin_id, f"🆕 <b>Новое агентство</b> #{row.id}: {escape(row.name)} · "
                                             f"{escape(row.city or '')}\n🤖 @{row.bot_username} · {row.channel_id}\n"
                                             f"👤 {who} (id <code>{callback.from_user.id}</code>)")
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось уведомить владельца платформы")


@router.message(Command("my"))
async def my_agencies(message: Message, state: FSMContext, session: AsyncSession) -> None:
    lang = await _lang(state)
    rows = list(await session.scalars(select(Agency).where(Agency.owner_telegram_id == message.from_user.id)))
    if not rows:
        await message.answer(t("my_none", lang))
        return
    await message.answer("\n".join(
        t("my_line", lang, name=escape(r.name), username=r.bot_username or "—",
          status=t("status_active" if r.status != "disabled" else "status_disabled", lang)) for r in rows))
