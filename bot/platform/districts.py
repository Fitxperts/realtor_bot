"""/districts в боте платформы: свои районы/массивы агентства.

Для Ферганы и Ташкента справочник встроен (bot/services/geo.py); для других
городов агентство присылает список само — по строке на массив, можно с
вариантами написания: «Чиланзар: chilonzor, чилонзор». Список сразу работает
в фильтре каталога, в заявке клиента и в офлайн-разборе объявлений.
"""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.agency_config import from_row, register
from bot.database.models import Agency
from bot.platform.pay import payable_agencies
from bot.platform.texts import t
from bot.services import geo
from bot.states.platform_states import DistrictsForm

router = Router(name="platform_districts")


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def _lang(state: FSMContext) -> str:
    return (await state.get_data()).get("lang", "ru")


async def _own(session: AsyncSession, user_id: int, agency_id: int) -> Agency | None:
    return next((r for r in await payable_agencies(session, user_id) if r.id == agency_id), None)


async def _show(message: Message, state: FSMContext, row: Agency) -> None:
    lang = await _lang(state)
    current = geo.parse(row.districts) if row.districts else geo.for_city(row.city)
    if current:
        head = t("dist_current", lang, name=escape(row.name), n=len(current),
                 list=escape(geo.format_(current)))
    else:
        head = t("dist_empty", lang, name=escape(row.name), city=escape(row.city or "—"))
    kb = None
    if row.districts and geo.for_city(row.city):
        kb = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("dist_reset_btn", lang, city=row.city),
                                                         f"dist:reset:{row.id}")]])
    await state.set_state(DistrictsForm.text)
    await state.update_data(dist_agency=row.id)
    await message.answer(head + "\n\n" + t("dist_howto", lang, max=geo.MAX_DISTRICTS), reply_markup=kb)


@router.message(Command("districts"))
async def districts(message: Message, state: FSMContext, session: AsyncSession) -> None:
    rows = await payable_agencies(session, message.from_user.id)
    if not rows:
        await message.answer(t("pay_none", await _lang(state)))
    elif len(rows) == 1:
        await _show(message, state, rows[0])
    else:
        await message.answer(t("pay_choose_agency", await _lang(state)), reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_btn(f"🏢 {r.name}", f"dist:a:{r.id}")] for r in rows]))


@router.callback_query(F.data.startswith("dist:a:"))
async def chose_agency(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await callback.answer()
    raw = callback.data.split(":")[2]
    row = await _own(session, callback.from_user.id, int(raw)) if raw.isdigit() else None
    if row is not None:
        await _show(callback.message, state, row)


async def _save(session: AsyncSession, row: Agency, value: str | None) -> None:
    row.districts = value
    await session.commit()
    register(from_row(row))          # бот агентства видит новый список сразу


@router.callback_query(F.data.startswith("dist:reset:"))
async def reset(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    raw = callback.data.split(":")[2]
    row = await _own(session, callback.from_user.id, int(raw)) if raw.isdigit() else None
    if row is None:
        await callback.answer()
        return
    await _save(session, row, None)
    await state.set_state(None)
    await callback.answer()
    await callback.message.answer(t("dist_saved", await _lang(state), n=len(geo.for_city(row.city)),
                                    list=escape(geo.format_(geo.for_city(row.city)))))


@router.message(DistrictsForm.text, F.text)
async def got_list(message: Message, state: FSMContext, session: AsyncSession) -> None:
    lang = await _lang(state)
    if message.text.startswith("/"):
        await state.set_state(None)
        await message.answer(t("cancelled", lang))
        return
    parsed = geo.parse(message.text)
    row = await _own(session, message.from_user.id, (await state.get_data()).get("dist_agency", 0))
    if row is None:
        await state.set_state(None)
        return
    if not parsed:
        await message.answer(t("dist_howto", lang, max=geo.MAX_DISTRICTS))
        return
    await _save(session, row, geo.format_(parsed))
    await state.set_state(None)
    await message.answer(t("dist_saved", lang, n=len(parsed), list=escape(geo.format_(parsed))))
