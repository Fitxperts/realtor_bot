"""Заявки с канала: пользователь жмёт кнопку под постом → deep-link в бота.

Поток: показываем объект → просим телефон → создаём клиента (CRM), привязанного
к типу/району/цене объекта, и уведомляем админов. Клик по кнопке тоже сразу
уходит админам как «тёплый» лид.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.config import get_settings
from bot.database import crud
from bot.database.models import ClientDealType, PropertyType
from bot.states.lead_states import LeadForm
from bot.utils.formatters import format_property_brief, format_property_card
from bot.utils.validators import is_valid_phone

logger = logging.getLogger(__name__)
router = Router(name="lead")

# Тип сделки объекта → тип сделки клиента
_DEAL_MAP = {PropertyType.rent: ClientDealType.rent, PropertyType.sale: ClientDealType.buy}


async def begin_lead(
    message: Message, state: FSMContext, session: AsyncSession, lang: str, property_id: str, bot: Bot,
    *, user=None,
) -> None:
    """Старт заявки по объекту: deep-link из канала ИЛИ кнопка в каталоге бота.

    Для callback из каталога `message` — сообщение бота, поэтому реального
    пользователя передаём через `user` (иначе взяли бы личность бота).
    """
    await state.clear()
    actor = user or message.from_user
    prop = await crud.get_property(session, property_id)
    if prop is None:
        await message.answer(i18n.t("lead_not_found", lang))
        return

    await message.answer(i18n.t("lead_intro", lang) + "\n\n" + format_property_card(prop, lang))
    await state.set_state(LeadForm.phone)
    await state.update_data(lead_property_id=property_id)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=i18n.btn("send_phone", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(i18n.t("lead_ask_phone", lang), reply_markup=kb)

    # Клик по кнопке — сразу тёплый лид админам
    who = escape(actor.full_name or "")
    uname = f" (@{escape(actor.username)})" if actor.username else ""
    await _notify_admins(bot, f"🎯 <b>Лид</b> по {escape(prop.id)}\n{who}{uname} нажал «Оставить заявку».")


@router.message(LeadForm.phone, F.contact)
async def lead_phone_contact(message: Message, state: FSMContext, session: AsyncSession, bot: Bot, lang: str) -> None:
    await _finish_lead(message, state, session, bot, lang, message.contact.phone_number)


@router.message(LeadForm.phone, F.text)
async def lead_phone_text(message: Message, state: FSMContext, session: AsyncSession, bot: Bot, lang: str) -> None:
    if not is_valid_phone(message.text):
        await message.answer(i18n.t("phone_bad", lang))
        return
    await _finish_lead(message, state, session, bot, lang, message.text.strip())


async def _finish_lead(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, lang: str, phone: str
) -> None:
    data = await state.get_data()
    prop = await crud.get_property(session, data.get("lead_property_id", ""))
    await state.clear()

    if prop is not None:
        client = await crud.create_client(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name,
            phone=phone,
            deal_type=_DEAL_MAP.get(prop.type, ClientDealType.rent),
            district=prop.district,
            budget=prop.price,
            currency="сум",
            residents=f"Заявка по объекту {prop.id}",
        )
        await _notify_admins(
            bot,
            f"🎯 <b>Заявка по объекту</b>\nОбъект: {format_property_brief(prop)}\n"
            f"Клиент: {escape(client.id)} · {escape(client.name or '—')} · 📞 {escape(phone)}",
        )

    await message.answer(i18n.t("lead_thanks", lang), reply_markup=ReplyKeyboardRemove())


# Заглушка на случай, если объект уже удалён, а пользователь всё же в состоянии
@router.callback_query(LeadForm.phone)
async def _noop(callback: CallbackQuery) -> None:
    await callback.answer()


async def _notify_admins(bot: Bot, text: str) -> None:
    for admin_id in get_settings().admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось уведомить админа %s", admin_id)
