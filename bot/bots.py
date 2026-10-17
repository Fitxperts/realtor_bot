"""Реестр ботов платформы: один движок — много ботов агентств.

- агентство №1 — основной бот из BOT_TOKEN (как было до SaaS);
- остальные — из таблицы agencies (токен зашифрован, bot/crypto.py).
Middleware AgencyMiddleware ставит текущее агентство по боту, получившему апдейт;
планировщик запускает задачи агентств через for_each_agency.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import crypto
from bot.agency_config import cfg
from bot.tenancy import DEFAULT_AGENCY_ID, agency_scope, reset_agency, set_agency

logger = logging.getLogger(__name__)

_by_agency: dict[int, Bot] = {}
_agency_by_bot: dict[int, int] = {}
_secrets: dict[int, str] = {}
# Что сделать с ботом нового агентства, подключённого на ходу (main ставит: вебхук + меню)
_activation_hook: Callable[[int, Bot], Awaitable[None]] | None = None


def make_bot(token: str) -> Bot:
    from bot.net_session import RobustSession

    return Bot(token=token, session=RobustSession(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def register_bot(agency_id: int, bot: Bot, secret: str | None = None) -> None:
    _by_agency[agency_id] = bot
    _agency_by_bot[bot.id] = agency_id
    if secret:
        _secrets[agency_id] = secret


def unregister_agency(agency_id: int) -> Bot | None:
    bot = _by_agency.pop(agency_id, None)
    _secrets.pop(agency_id, None)
    if bot is not None:
        _agency_by_bot.pop(bot.id, None)
    return bot


def secret_for(agency_id: int) -> str | None:
    return _secrets.get(agency_id)


def set_activation_hook(hook: Callable[[int, Bot], Awaitable[None]] | None) -> None:
    global _activation_hook
    _activation_hook = hook


async def activate(agency_id: int, token: str, secret: str) -> Bot:
    """Подключить бота агентства на ходу (после онбординга) — без перезапуска."""
    old = unregister_agency(agency_id)
    if old is not None:
        await old.session.close()
    bot = make_bot(token)
    register_bot(agency_id, bot, secret)
    if _activation_hook is not None:
        await _activation_hook(agency_id, bot)
    return bot


def bot_for(agency_id: int) -> Bot | None:
    return _by_agency.get(agency_id)


def agency_for_bot(bot_id: int) -> int:
    return _agency_by_bot.get(bot_id, DEFAULT_AGENCY_ID)


def agency_bots() -> dict[int, Bot]:
    return dict(_by_agency)


def clear() -> None:
    _by_agency.clear()
    _agency_by_bot.clear()
    _secrets.clear()


async def load_agency_bots(session: AsyncSession) -> list[int]:
    """Создать Bot для каждого активного агентства с токеном (кроме №1 — у него BOT_TOKEN)."""
    from bot.database.models import Agency

    loaded = []
    rows = await session.scalars(select(Agency).where(Agency.id != DEFAULT_AGENCY_ID,
                                                       Agency.bot_token_enc.is_not(None)))
    for row in rows:
        if row.status == "disabled":
            continue
        try:
            register_bot(row.id, make_bot(crypto.decrypt(row.bot_token_enc)), row.webhook_secret)
            loaded.append(row.id)
        except Exception as exc:  # noqa: BLE001 - одно сломанное агентство не роняет остальные
            logger.error("Агентство %s: бот не загружен (%s)", row.id, exc)
    return loaded


class AgencyMiddleware(BaseMiddleware):
    """Апдейт пришёл боту агентства N → всё, что дальше, работает в контексте агентства N."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        agency_id = agency_for_bot(data["bot"].id)
        token = set_agency(agency_id)
        try:
            if not cfg(agency_id).active:
                logger.info("Агентство %s отключено — апдейт пропущен", agency_id)
                return None
            return await handler(event, data)
        finally:
            reset_agency(token)


async def for_each_agency(job: Callable[..., Awaitable[Any]], *args: Any) -> None:
    """Задача планировщика для каждого активного агентства — с его ботом и в его контексте."""
    for agency_id, bot in agency_bots().items():
        if not cfg(agency_id).active:
            continue
        with agency_scope(agency_id):
            try:
                await job(bot, *args)
            except Exception:  # noqa: BLE001 - сбой у одного агентства не мешает остальным
                logger.exception("Задача %s упала у агентства %s", getattr(job, "__name__", job), agency_id)
