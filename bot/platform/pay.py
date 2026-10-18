"""/pay в боте платформы: агентство → тариф → срок → счёт и ссылки Payme / Click.

Платить может хозяин агентства или его админ. Сумма и срок фиксируются в счёте
(Payment) — платёжная система проверяет именно их; продление делает
bot/services/payments.py, когда Payme / Click подтвердит оплату.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings, get_settings
from bot.database.models import Agency
from bot.platform.texts import t
from bot.services import billing, payments
from bot.tenancy import DEFAULT_AGENCY_ID

logger = logging.getLogger(__name__)
router = Router(name="platform_pay")


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def _lang(state: FSMContext) -> str:
    return (await state.get_data()).get("lang", "ru")


def can_pay(row: Agency, user_id: int) -> bool:
    return row.id != DEFAULT_AGENCY_ID and (
        row.owner_telegram_id == user_id or user_id in Settings._parse_admin_ids(row.admin_ids or ""))


async def payable_agencies(session: AsyncSession, user_id: int) -> list[Agency]:
    rows = await session.scalars(select(Agency).where(Agency.id != DEFAULT_AGENCY_ID).order_by(Agency.id))
    return [r for r in rows if can_pay(r, user_id)]


async def _agency(session: AsyncSession, agency_id: str, user_id: int) -> Agency | None:
    row = await session.get(Agency, int(agency_id)) if agency_id.isdigit() else None
    return row if row is not None and can_pay(row, user_id) else None


def _status_line(row: Agency, lang: str) -> str:
    state, until = billing.compute_state(row.id, row.trial_until, row.paid_until, row.created_at)
    plan = billing.PLANS.get(row.plan, {}).get("name", row.plan)
    return t(f"pay_state_{state.value}", lang, name=escape(row.name), plan=plan,
             until=f"{until:%d.%m.%Y}" if until else "—")


async def _show_plans(message: Message, row: Agency, lang: str) -> None:
    rows = [[_btn(t("pay_plan_btn", lang, name=p["name"], price=payments.money(p["uzs"])), f"pay:p:{row.id}:{key}")]
            for key, p in billing.PLANS.items()]
    await message.answer(_status_line(row, lang) + "\n\n" + t("pay_choose_plan", lang),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("pay"))
async def pay(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.set_state(None)                    # прервать начатый ввод (например, списка районов)
    lang = await _lang(state)
    rows = await payable_agencies(session, message.from_user.id)
    if not rows:
        await message.answer(t("pay_none", lang))
    elif len(rows) == 1:
        await _show_plans(message, rows[0], lang)
    else:
        await message.answer(t("pay_choose_agency", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [_btn(f"🏢 {r.name}", f"pay:a:{r.id}")] for r in rows]))


@router.callback_query(F.data.startswith("pay:a:"))
async def chose_agency(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await callback.answer()
    row = await _agency(session, callback.data.split(":")[2], callback.from_user.id)
    if row is not None:
        await _show_plans(callback.message, row, await _lang(state))


@router.callback_query(F.data.startswith("pay:p:"))
async def chose_plan(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await callback.answer()
    _, _, agency_id, plan = callback.data.split(":")
    row = await _agency(session, agency_id, callback.from_user.id)
    if row is None or plan not in billing.PLANS:
        return
    lang = await _lang(state)
    buttons = [_btn(t("pay_months_btn", lang, n=m, total=payments.money(payments.price(plan, m))),
                    f"pay:m:{row.id}:{plan}:{m}") for m in payments.MONTH_OPTIONS]
    await callback.message.answer(t("pay_choose_months", lang, plan=billing.PLANS[plan]["name"]),
                                  reply_markup=InlineKeyboardMarkup(inline_keyboard=[[b] for b in buttons]))


@router.callback_query(F.data.startswith("pay:m:"))
async def chose_months(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    await callback.answer()
    _, _, agency_id, plan, months = callback.data.split(":")
    row = await _agency(session, agency_id, callback.from_user.id)
    if row is None or plan not in billing.PLANS or not months.isdigit() \
            or int(months) not in payments.MONTH_OPTIONS:
        return
    lang = await _lang(state)
    invoice = await payments.create_invoice(session, row.id, plan, int(months), payer_id=callback.from_user.id)
    me = await bot.me()
    back = f"https://t.me/{me.username}" if me.username else ""
    links = [("Payme", payments.payme_link(invoice, back)), ("Click", payments.click_link(invoice, back))]
    links = [(name, url) for name, url in links if url]
    text = t("pay_invoice", lang, id=invoice.id, name=escape(row.name), plan=billing.PLANS[plan]["name"],
             months=invoice.months, total=payments.money(invoice.amount))
    if not links:   # платёжные системы ещё не подключены — счёт сохранён, владелец платформы оформит вручную
        await callback.message.answer(text + "\n\n" + t("pay_offline", lang))
        for admin_id in get_settings().admin_ids:
            try:
                await bot.send_message(admin_id, f"🧾 Счёт №{invoice.id}: агентство #{row.id} {escape(row.name)}, "
                                                 f"«{billing.PLANS[plan]['name']}» × {invoice.months} мес, "
                                                 f"{payments.money(invoice.amount)}. Онлайн-оплата не подключена "
                                                 f"(PAYME_*/CLICK_* в .env) — свяжитесь с агентством.")
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось уведомить владельца платформы о счёте")
        return
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 {name}", url=url)] for name, url in links]))
