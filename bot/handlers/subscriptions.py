"""Подписки на поиск: «🔔 Мои подписки», отписка и подписка на похожие.

Подписка = заявка клиента (анкета «Подобрать жильё»). Уведомления рассылает
bot/services/alerts.py при публикации объекта.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.database.models import Client, ClientDealType, ClientStatus
from bot.handlers.client import begin_client_form
from bot.utils.formatters import format_money

router = Router(name="subscriptions")


def describe(client: Client, lang: str) -> str:
    deal = i18n.t("deal_rent_word" if client.deal_type == ClientDealType.rent else "deal_buy_word", lang)
    budget = (i18n.t("sub_budget", lang, amount=format_money(client.budget, client.currency))
              if client.budget else "")
    return i18n.t("sub_line", lang, deal=deal, district=client.district or i18n.t("cc_any_district", lang),
                  budget=budget)


async def active_subscriptions(session: AsyncSession, telegram_id: int) -> list[Client]:
    stmt = (select(Client)
            .where(Client.telegram_id == telegram_id, Client.notify.is_(True),
                   Client.status != ClientStatus.closed)
            .order_by(Client.created_at.desc()))
    return list(await session.scalars(stmt))


def _new_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=i18n.btn("sub_new", lang), callback_data="sub:new")
    ]])


@router.message(F.text.in_(i18n.btn_variants("subs")))
async def my_subscriptions(message: Message, session: AsyncSession, lang: str) -> None:
    subs = await active_subscriptions(session, message.from_user.id)
    if not subs:
        await message.answer(i18n.t("subs_empty", lang), reply_markup=_new_kb(lang))
        return
    await message.answer(i18n.t("subs_title", lang))
    for client in subs:
        await message.answer(describe(client, lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=i18n.btn("sub_off", lang), callback_data=f"sub:off:{client.id}")
        ]]))
    await message.answer("➕", reply_markup=_new_kb(lang))


async def _own_client(callback: CallbackQuery, session: AsyncSession) -> Client | None:
    client = await session.get(Client, callback.data.split(":", 2)[2])
    return client if client and client.telegram_id == callback.from_user.id else None


@router.callback_query(F.data.startswith("sub:off:"))
async def unsubscribe(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    client = await _own_client(callback, session)
    if client is not None:
        client.notify = False
        await session.commit()
    await callback.answer(i18n.t("sub_off_done", lang), show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("sub:on:"))
async def subscribe_similar(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    client = await _own_client(callback, session)
    if client is not None:
        client.notify = True
        await session.commit()
    await callback.answer(i18n.t("sub_on_done", lang), show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data == "sub:new")
async def new_subscription(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await begin_client_form(callback.message, state, lang, full_name=callback.from_user.full_name)
