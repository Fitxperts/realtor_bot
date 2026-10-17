"""Демо-бот (DEMO_MODE): приветствие, «Хочу такой бот» и показ «как видит агентство».

Посетитель пробует обе стороны, видя только свои данные:
- оставил заявку → видит, как она приходит агентству;
- разместил объявление → оно сразу в демо-канале + видно, как агентство
  получает его на модерацию;
- «Хочу такой бот» → телефон уходит тебе как тёплый лид на SaaS.
Роутер подключается только при DEMO_MODE=true (bot/handlers/__init__.py).
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.config import get_settings
from bot.database import crud
from bot.database.models import Property, PropertyStatus, UserRole
from bot.keyboards import common_kb
from bot.services import publisher
from bot.services.notify import notify_admins
from bot.states.demo_states import DemoForm
from bot.utils.formatters import format_property_card
from bot.utils.media import photo_input
from bot.utils.validators import is_valid_phone

logger = logging.getLogger(__name__)
router = Router(name="demo")


def welcome_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.t("catalog_browse", lang), callback_data="cat:deal:all")],
        [InlineKeyboardButton(text=i18n.btn("role_client", lang), callback_data="role:client")],
        [InlineKeyboardButton(text=i18n.btn("add_object", lang), callback_data="role:owner")],
        [InlineKeyboardButton(text=i18n.btn("demo_want", lang), callback_data="demo:want")],
    ])


async def send_welcome(message: Message, lang: str) -> None:
    await message.answer(i18n.t("demo_welcome", lang), reply_markup=welcome_kb(lang))


def _admin_preview_kb(lang: str) -> InlineKeyboardMarkup:
    """Кнопки как у администратора — в демо только для вида."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.btn("adm_approve", lang), callback_data="demo:noop"),
         InlineKeyboardButton(text=i18n.btn("adm_edit", lang), callback_data="demo:noop")],
        [InlineKeyboardButton(text=i18n.btn("adm_hold", lang), callback_data="demo:noop")],
    ])


def post_link(channel_id: str, post_id: int | None) -> str | None:
    """Ссылка на пост: @channel → t.me/channel/N, -100… → t.me/c/…/N."""
    if not channel_id or not post_id:
        return None
    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id[1:]}/{post_id}"
    if channel_id.startswith("-100"):
        return f"https://t.me/c/{channel_id[4:]}/{post_id}"
    return None


def _who(user: User) -> str:
    uname = f" (@{escape(user.username)})" if user.username else ""
    return f"{escape(user.full_name or '')}{uname}, id=<code>{user.id}</code>"


# ---------------------------------------------------------------------------
# «Хочу такой бот» → телефон → тебе
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "demo:want")
async def want_callback(callback: CallbackQuery, state: FSMContext, bot: Bot, lang: str) -> None:
    await callback.answer()
    await _ask_phone(callback.message, state, bot, lang, callback.from_user)


@router.message(F.text.in_(i18n.btn_variants("demo_want")))
async def want_button(message: Message, state: FSMContext, bot: Bot, lang: str) -> None:
    await _ask_phone(message, state, bot, lang, message.from_user)


async def _ask_phone(message: Message, state: FSMContext, bot: Bot, lang: str, user: User) -> None:
    await state.set_state(DemoForm.phone)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=i18n.btn("send_phone", lang), request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await message.answer(i18n.t("demo_want_ask", lang), reply_markup=kb)
    await notify_admins(bot, f"💼 <b>Демо → хочет такой бот</b>\n{_who(user)}")


@router.message(DemoForm.phone, F.contact)
async def want_contact(message: Message, state: FSMContext, session: AsyncSession, bot: Bot, lang: str) -> None:
    await _finish_want(message, state, session, bot, lang, message.contact.phone_number)


@router.message(DemoForm.phone, F.text)
async def want_text(message: Message, state: FSMContext, session: AsyncSession, bot: Bot, lang: str) -> None:
    if not is_valid_phone(message.text):
        await message.answer(i18n.t("phone_bad", lang))
        return
    await _finish_want(message, state, session, bot, lang, message.text.strip())


async def _finish_want(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, lang: str, phone: str
) -> None:
    await state.clear()
    await notify_admins(bot, f"💼 <b>Демо-лид на бота</b>\n{_who(message.from_user)}\n📞 {escape(phone)}")
    user = await crud.get_user(session, message.from_user.id)
    menu = common_kb.owner_menu(lang) if user and user.role == UserRole.owner else common_kb.client_menu(lang)
    await message.answer(i18n.t("demo_want_thanks", lang), reply_markup=menu)


@router.callback_query(F.data == "demo:noop")
async def noop(callback: CallbackQuery, lang: str) -> None:
    await callback.answer(i18n.t("demo_noop", lang), show_alert=True)


# ---------------------------------------------------------------------------
# Вызывается из других сценариев
# ---------------------------------------------------------------------------
async def show_lead_view(message: Message, lang: str, admin_text: str) -> None:
    await message.answer(i18n.t("demo_lead_view", lang) + "\n\n" + admin_text)


async def publish_visitor_listing(
    message: Message, session: AsyncSession, bot: Bot, lang: str, prop: Property, user: User
) -> None:
    """Объявление посетителя — сразу в демо-канал (без модерации) + показ админской карточки."""
    channel = get_settings().channel_id
    link = None
    if channel:
        try:
            published = await publisher.publish_property(bot, session, prop.id)
            prop = published or prop
            link = post_link(channel, prop.channel_post_id)
        except Exception:  # noqa: BLE001 - канал недоступен: объект всё равно виден в каталоге
            logger.exception("Демо: не удалось опубликовать %s в канал", prop.id)
    if prop.status != PropertyStatus.active:
        prop.status = PropertyStatus.active
        await session.commit()

    if link:
        await message.answer(i18n.t("demo_published", lang, id=escape(prop.id), link=link))
    else:
        await message.answer(i18n.t("demo_saved", lang, id=escape(prop.id)))

    text = i18n.t("demo_mod_header", lang) + "\n\n" + format_property_card(prop, lang)
    kb = _admin_preview_kb(lang)
    photos = prop.photo_list
    try:
        if photos:
            await message.answer_photo(photo_input(photos[0]), caption=text[:1024], reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await message.answer(text, reply_markup=kb)

    await notify_admins(
        bot, f"💼 <b>Демо</b>: {_who(user)} разместил объявление {escape(prop.id)}" + (f"\n{link}" if link else "")
    )
