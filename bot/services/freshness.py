"""Проверка актуальности: проданное и сданное уходит из каталога само.

Раз в день (11:00):
1. Объект активен 14 дней без подтверждения → владельцу «Ещё актуально?»
   (у объектов из авто-репоста владельца нет — спрашиваем админов).
2. Спросили 3 дня назад, ответа нет → снимаем: архив + пометка поста в канале,
   владельцу — сообщение с кнопкой «вернуть».
Не больше ASK_PER_RECIPIENT вопросов одному человеку за раз (старые — первыми),
чтобы первый запуск после обновления не завалил админов сообщениями.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections import Counter

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.config import get_settings
from bot.database import crud
from bot.database.models import Property, PropertyStatus
from bot.database.session import get_sessionmaker
from bot.services import publisher
from bot.services.notify import notify_admins
from bot.utils import timeutils
from bot.utils.formatters import format_property_card

logger = logging.getLogger(__name__)

ASK_AFTER_DAYS = 14
ARCHIVE_AFTER_DAYS = 3
ASK_PER_RECIPIENT = 5


def freshness_kb(property_id: str, lang: str, *, only_yes: bool = False) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text=i18n.btn("fresh_yes", lang), callback_data=f"fr:yes:{property_id}")]
    if not only_yes:
        row.append(InlineKeyboardButton(text=i18n.btn("fresh_sold", lang), callback_data=f"fr:sold:{property_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def _lang(session: AsyncSession, telegram_id: int) -> str:
    user = await crud.get_user(session, telegram_id)
    return i18n.normalize(user.language if user else None)


def _recipients(prop: Property) -> list[int]:
    return [prop.owner_telegram_id] if prop.owner_telegram_id else list(get_settings().admin_ids)


async def ask_owners(bot: Bot, session: AsyncSession) -> int:
    """Спросить «ещё актуально?» про объекты старше 14 дней. Возвращает число объектов."""
    now = timeutils.now()
    border = now - dt.timedelta(days=ASK_AFTER_DAYS)
    stmt = (select(Property)
            .where(Property.status == PropertyStatus.active, Property.freshness_asked_at.is_(None),
                   func.coalesce(Property.confirmed_at, Property.created_at) <= border)
            .order_by(func.coalesce(Property.confirmed_at, Property.created_at)))
    per_recipient: Counter[int] = Counter()
    asked = 0
    for prop in await session.scalars(stmt):
        recipients = _recipients(prop)
        if not recipients or any(per_recipient[r] >= ASK_PER_RECIPIENT for r in recipients):
            continue  # остальным — завтра
        delivered = False
        for chat_id in recipients:
            per_recipient[chat_id] += 1
            lang = await _lang(session, chat_id)
            text = i18n.t("fresh_ask", lang, id=prop.id) + "\n\n" + format_property_card(prop, lang)
            try:
                await bot.send_message(chat_id, text[:4096], reply_markup=freshness_kb(prop.id, lang))
                delivered = True
            except Exception:  # noqa: BLE001 - заблокировал бота: всё равно отсчитываем 3 дня
                logger.debug("Не удалось спросить %s про %s", chat_id, prop.id)
            await asyncio.sleep(0.05)  # бережём лимиты Telegram
        prop.freshness_asked_at = now
        await session.commit()
        asked += 1
        if not delivered:
            logger.info("Актуальность %s: вопрос не доставлен (бот заблокирован?)", prop.id)
    return asked


async def archive_unanswered(bot: Bot, session: AsyncSession) -> int:
    """Снять объекты, по которым 3 дня нет ответа. Возвращает число снятых."""
    border = timeutils.now() - dt.timedelta(days=ARCHIVE_AFTER_DAYS)
    stmt = select(Property).where(
        Property.status == PropertyStatus.active,
        Property.freshness_asked_at.is_not(None),
        Property.freshness_asked_at <= border,
        or_(Property.confirmed_at.is_(None), Property.confirmed_at < Property.freshness_asked_at),
    )
    archived = 0
    for prop in list(await session.scalars(stmt)):
        await publisher.archive_stale(bot, session, prop.id)
        archived += 1
        if prop.owner_telegram_id:
            lang = await _lang(session, prop.owner_telegram_id)
            try:
                await bot.send_message(prop.owner_telegram_id, i18n.t("fresh_archived", lang, id=prop.id),
                                       reply_markup=freshness_kb(prop.id, lang, only_yes=True))
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось сообщить владельцу %s", prop.owner_telegram_id)
    return archived


async def run_freshness(bot: Bot) -> None:
    """Задача планировщика: сначала снимаем просроченные, потом задаём новые вопросы."""
    async with get_sessionmaker()() as session:
        archived = await archive_unanswered(bot, session)
        asked = await ask_owners(bot, session)
    if archived or asked:
        await notify_admins(bot, f"🕑 Проверка актуальности: спросили про {asked} объект(ов), "
                                 f"сняли без ответа — {archived}.")
    logger.info("Актуальность: спросили %d, сняли %d", asked, archived)
