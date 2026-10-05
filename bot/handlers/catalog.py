"""Каталог: просмотр активных объявлений прямо в боте (лентой).

Решает проблему «пустого бота»: клиент может листать реальные объекты, а не
только заполнять анкету. У каждой карточки — кнопка «Оставить заявку», которая
заводит клиента в CRM (через общий поток lead). Фильтр по типу сделки.
"""
from __future__ import annotations

import logging
import os

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.database import crud
from bot.database.models import PropertyType
from bot.handlers.client import begin_client_form
from bot.handlers.lead import begin_lead
from bot.utils.formatters import format_property_card

logger = logging.getLogger(__name__)
router = Router(name="catalog")

PREFIX = "cat"
PAGE = 3  # объявлений за один показ
_CAPTION_LIMIT = 1024

_TYPE = {"rent": PropertyType.rent, "sale": PropertyType.sale, "all": None}


def _deal_filter_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=i18n.btn("deal_rent", lang), callback_data=f"{PREFIX}:deal:rent"),
        InlineKeyboardButton(text=i18n.btn("deal_buy", lang), callback_data=f"{PREFIX}:deal:sale"),
        InlineKeyboardButton(text=i18n.btn("deal_all", lang), callback_data=f"{PREFIX}:deal:all"),
    ]])


def _card_kb(property_id: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=i18n.btn("lead_cta", lang), callback_data=f"{PREFIX}:lead:{property_id}")
    ]])


def _photo_input(photo: str):
    """Локальный путь (авто-репост) → FSInputFile, иначе file_id как есть."""
    return FSInputFile(photo) if os.path.exists(photo) else photo


# --- Вход: кнопка меню «Объявления» или инлайн «Смотреть объявления» ---------
@router.message(F.text.in_(i18n.btn_variants("catalog")))
async def catalog_menu(message: Message, lang: str) -> None:
    await message.answer(i18n.t("catalog_pick", lang), reply_markup=_deal_filter_kb(lang))


@router.callback_query(F.data.startswith(f"{PREFIX}:deal:"))
async def catalog_deal(callback: CallbackQuery, session: AsyncSession, bot: Bot, lang: str) -> None:
    deal = callback.data.split(":")[2]
    await callback.answer()
    await _show_batch(callback.message, session, bot, lang, deal, 0)


@router.callback_query(F.data.startswith(f"{PREFIX}:more:"))
async def catalog_more(callback: CallbackQuery, session: AsyncSession, bot: Bot, lang: str) -> None:
    _, _, deal, offset = callback.data.split(":")
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)  # гасим старую «Показать ещё»
    await _show_batch(callback.message, session, bot, lang, deal, int(offset))


@router.callback_query(F.data.startswith(f"{PREFIX}:lead:"))
async def catalog_lead(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot, lang: str
) -> None:
    property_id = callback.data.split(":", 2)[2]
    await callback.answer()
    await begin_lead(callback.message, state, session, lang, property_id, bot, user=callback.from_user)


@router.callback_query(F.data == f"{PREFIX}:req")
async def catalog_request(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await begin_client_form(callback.message, state, lang, full_name=callback.from_user.full_name)


async def _show_batch(
    message: Message, session: AsyncSession, bot: Bot, lang: str, deal: str, offset: int
) -> None:
    prop_type = _TYPE.get(deal)
    total = await crud.count_active_properties(session, prop_type=prop_type)
    if total == 0:
        await message.answer(
            i18n.t("catalog_empty", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=i18n.btn("lead_cta", lang), callback_data=f"{PREFIX}:req")
            ]]),
        )
        return

    page = await crud.active_properties_page(session, prop_type=prop_type, offset=offset, limit=PAGE)
    for prop in page:
        caption = format_property_card(prop, lang)[:_CAPTION_LIMIT]
        kb = _card_kb(prop.id, lang)
        photos = prop.photo_list
        try:
            if photos:
                await message.answer_photo(_photo_input(photos[0]), caption=caption, reply_markup=kb)
            else:
                await message.answer(caption, reply_markup=kb)
        except Exception:  # noqa: BLE001 - битое фото не должно рвать выдачу
            await message.answer(caption, reply_markup=kb)

    shown = offset + len(page)
    if shown < total:
        await message.answer(
            i18n.t("catalog_footer", lang, shown=shown, total=total),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=i18n.btn("cat_more", lang),
                                     callback_data=f"{PREFIX}:more:{deal}:{shown}")
            ]]),
        )
    else:
        await message.answer(
            i18n.t("catalog_end", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=i18n.btn("lead_cta", lang), callback_data=f"{PREFIX}:req")
            ]]),
        )
