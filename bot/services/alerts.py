"""Подписки на поиск: новый объект → подходящим клиентам.

Заявка клиента (анкета «Подобрать жильё») — это и есть подписка. Когда объект
публикуется, бот присылает его тем, чей запрос подходит, на языке клиента и с
кнопками «Оставить заявку» и «Отписаться». Антиспам:
- один объект одному человеку — не больше одного раза (search_alerts);
- не больше ALERTS_DAILY_LIMIT уведомлений в сутки на человека;
- владельцу объекта его же объект не присылаем.
"""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.config import get_settings
from bot.database.models import BotUser, Client, Property, SearchAlert
from bot.services import analytics, matcher
from bot.utils import timeutils
from bot.utils.formatters import format_property_card
from bot.utils.media import photo_input

logger = logging.getLogger(__name__)


def alert_kb(property_id: str, client_id: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.btn("lead_cta", lang), callback_data=f"cat:lead:{property_id}")],
        [InlineKeyboardButton(text=i18n.btn("sub_off", lang), callback_data=f"sub:off:{client_id}")],
    ])


async def _language(session: AsyncSession, telegram_id: int) -> str:
    user = await session.get(BotUser, telegram_id)
    return i18n.normalize(user.language if user else None)


async def _already_sent(session: AsyncSession, telegram_id: int, property_id: str) -> bool:
    stmt = select(SearchAlert.id).where(SearchAlert.telegram_id == telegram_id,
                                        SearchAlert.property_id == property_id)
    return await session.scalar(stmt) is not None


async def _sent_today(session: AsyncSession, telegram_id: int) -> int:
    since = timeutils.now() - dt.timedelta(days=1)
    stmt = select(func.count()).select_from(SearchAlert).where(
        SearchAlert.telegram_id == telegram_id, SearchAlert.sent_at >= since)
    return int(await session.scalar(stmt) or 0)


async def _deliver(bot: Bot, prop: Property, client: Client, lang: str) -> bool:
    text = i18n.t("alert_new", lang) + "\n\n" + format_property_card(prop, lang)
    kb = alert_kb(prop.id, client.id, lang)
    photos = prop.photo_list
    try:
        if photos:
            try:
                await bot.send_photo(client.telegram_id, photo_input(photos[0]), caption=text[:1024],
                                     reply_markup=kb)
                return True
            except TelegramBadRequest:  # битое фото — всё равно сообщаем текстом
                pass
        await bot.send_message(client.telegram_id, text, reply_markup=kb)
        return True
    except Exception:  # noqa: BLE001 - клиент заблокировал бота и т.п.
        logger.debug("Не удалось уведомить клиента %s", client.telegram_id)
        return False


async def send_property_alerts(bot: Bot, session: AsyncSession, prop: Property) -> int:
    """Разослать объект по подпискам. Возвращает число отправленных уведомлений."""
    limit = get_settings().alerts_daily_limit
    seen: set[int] = set()
    sent = 0
    for client in await matcher.clients_for_property(session, prop):
        tg = client.telegram_id
        if tg in seen or tg == prop.owner_telegram_id:
            continue
        seen.add(tg)  # несколько подходящих заявок у человека — одно уведомление
        if await _already_sent(session, tg, prop.id) or await _sent_today(session, tg) >= limit:
            continue
        if await _deliver(bot, prop, client, await _language(session, tg)):
            session.add(SearchAlert(telegram_id=tg, client_id=client.id, property_id=prop.id))
            await session.commit()
            await analytics.track("alert_sent", telegram_id=tg, property_id=prop.id)
            sent += 1
    if sent:
        logger.info("Объект %s: уведомлено по подпискам %d", prop.id, sent)
    return sent
