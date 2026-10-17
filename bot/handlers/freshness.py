"""Ответы на «Объявление ещё актуально?» (вопросы задаёт bot/services/freshness.py)."""
from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.agency_config import cfg
from bot.database import crud
from bot.database.models import Property, PropertyStatus
from bot.services import publisher
from bot.services.notify import notify_admins
from bot.utils import timeutils

router = Router(name="freshness")


async def _allowed_property(callback: CallbackQuery, session: AsyncSession) -> Property | None:
    prop = await crud.get_property(session, callback.data.split(":", 2)[2])
    user_id = callback.from_user.id
    if prop is None or not (prop.owner_telegram_id == user_id or cfg().is_admin(user_id)):
        return None
    return prop


@router.callback_query(F.data.startswith("fr:yes:"))
async def still_actual(callback: CallbackQuery, session: AsyncSession, bot: Bot, lang: str) -> None:
    prop = await _allowed_property(callback, session)
    if prop is None or prop.status not in (PropertyStatus.active, PropertyStatus.archived):
        await callback.answer(i18n.t("fresh_closed", lang), show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    was_archived = prop.status == PropertyStatus.archived
    prop.confirmed_at, prop.freshness_asked_at = timeutils.now(), None
    await session.commit()
    if was_archived:  # сняли по таймауту, а он ещё актуален — публикуем заново
        await publisher.publish_property(bot, session, prop.id)
    await callback.answer(i18n.t("fresh_restored" if was_archived else "fresh_yes_done", lang), show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("fr:sold:"))
async def closed(callback: CallbackQuery, session: AsyncSession, bot: Bot, lang: str) -> None:
    prop = await _allowed_property(callback, session)
    if prop is None or prop.status != PropertyStatus.active:
        await callback.answer(i18n.t("fresh_closed", lang), show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    await publisher.mark_as_rented(bot, session, prop.id)
    await callback.answer(i18n.t("fresh_sold_done", lang), show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)
    if not cfg().is_admin(callback.from_user.id):
        await notify_admins(bot, f"⛔ Владелец отметил объект {escape(prop.id)} как сданный/проданный "
                                 "(проверка актуальности).")
