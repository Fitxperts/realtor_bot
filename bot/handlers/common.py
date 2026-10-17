"""Общий роутер: язык, определение роли, меню, помощь и поддержка.

Логика входа:
- админ (ADMIN_TELEGRAM_ID) обрабатывается в admin-роутере (зарегистрирован выше);
- новый пользователь → выбор языка → выбор роли;
- вернувшийся → сразу открываем сценарий по сохранённой роли;
- кнопки «Сменить роль» и «Язык» в меню (на всех языках).
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.agency_config import cfg
from bot.config import get_settings
from bot.database import crud
from bot.database.models import BotUser, UserRole
from bot.handlers import demo
from bot.handlers.client import begin_client_form
from bot.handlers.owner import begin_property_form
from bot.keyboards import common_kb
from bot.services.notify import notify_admins
from bot.states.support_states import SupportForm
from bot.utils.formatters import format_property_card, property_status_label

logger = logging.getLogger(__name__)
router = Router(name="common")

# Множества подписей reply-кнопок на всех языках (для матчинга по тексту)
BTN_FIND = i18n.btn_variants("find_housing")
BTN_ADD = i18n.btn_variants("add_object")
BTN_MYOBJ = i18n.btn_variants("my_objects")
BTN_CHANGE_ROLE = i18n.btn_variants("change_role")
BTN_LANGUAGE = i18n.btn_variants("language")

# Тексты кнопок меню — исключаются из catch-all first_touch
_MENU_TEXTS = i18n.all_menu_texts()

# Приглашение выбрать язык — сразу на трёх языках, чтобы понял любой
_LANG_PROMPT = "🌐 Tilni tanlang / Выберите язык / Choose your language:"


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(
    message: Message, state: FSMContext, session: AsyncSession, lang: str, command: CommandObject
) -> None:
    await state.clear()
    user = await _ensure_user(message, session)

    # Deep-link «оставить заявку» из-под поста в канале: start=lead_<property_id>
    args = command.args or ""
    if args.startswith("lead_"):
        from bot.handlers.lead import begin_lead

        await begin_lead(message, state, session, lang, args[5:], message.bot)
        return

    if not user.language:
        await message.answer(_LANG_PROMPT, reply_markup=common_kb.language_kb())
        return
    if get_settings().demo_mode:
        await _demo_welcome(message, user, lang)
        return
    if not user.role_chosen:
        await message.answer(i18n.t("choose_role", lang), reply_markup=common_kb.role_choice_kb(lang))
    else:
        await _open_scenario(message, state, user.role, lang, message.from_user.full_name)


# ---------------------------------------------------------------------------
# Выбор / смена языка
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("lang:"))
async def choose_language(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    code = i18n.normalize(callback.data.split(":")[1])
    await crud.set_language(session, callback.from_user.id, code)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await callback.message.answer(i18n.t("language_saved", code, name=i18n.LANGUAGES[code]))

    user = await crud.get_user(session, callback.from_user.id)
    if get_settings().demo_mode:
        await _demo_welcome(callback.message, user, code)
    elif user and user.role_chosen:
        await _open_menu(callback.message, user.role, code)
    else:
        await callback.message.answer(i18n.t("choose_role", code), reply_markup=common_kb.role_choice_kb(code))


@router.message(F.text.in_(BTN_LANGUAGE))
async def change_language(message: Message) -> None:
    await message.answer(_LANG_PROMPT, reply_markup=common_kb.language_kb())


# ---------------------------------------------------------------------------
# Выбор / смена роли
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("role:"))
async def choose_role(callback: CallbackQuery, state: FSMContext, session: AsyncSession, lang: str) -> None:
    role = UserRole(callback.data.split(":")[1])
    await crud.choose_role(session, callback.from_user.id, role)
    await callback.message.edit_reply_markup(reply_markup=None)
    # ВАЖНО: имя берём у реального пользователя (callback.message.from_user == бот)
    await _open_scenario(callback.message, state, role, lang, callback.from_user.full_name)
    await callback.answer()


@router.message(F.text.in_(BTN_CHANGE_ROLE))
async def change_role(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    await message.answer(i18n.t("choose_role_short", lang), reply_markup=common_kb.role_choice_kb(lang))


# ---------------------------------------------------------------------------
# Кнопки меню ролей
# ---------------------------------------------------------------------------
@router.message(F.text.in_(BTN_FIND))
async def menu_client(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    await begin_client_form(message, state, lang, full_name=message.from_user.full_name)


@router.message(F.text.in_(BTN_ADD))
async def menu_owner(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    await begin_property_form(message, state, lang)


@router.message(F.text.in_(BTN_MYOBJ))
async def menu_my_objects(message: Message, session: AsyncSession, lang: str) -> None:
    props = await crud.list_owner_properties(session, message.from_user.id)
    if not props:
        await message.answer(i18n.t("no_objects", lang))
        return
    for prop in props:
        status = property_status_label(prop.status, lang)
        await message.answer(f"[{status}] " + format_property_card(prop, lang))


# ---------------------------------------------------------------------------
# /cancel, /help и поддержка
# ---------------------------------------------------------------------------
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, lang: str) -> None:
    current = await state.get_state()
    await state.clear()
    if current is None:
        await message.answer(i18n.t("cancel_none", lang))
    else:
        await message.answer(i18n.t("cancel_done", lang))


def _support_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=i18n.btn("support", lang), callback_data="support:start")]]
    )


@router.message(Command("help"))
async def cmd_help(message: Message, lang: str) -> None:
    await message.answer(i18n.t("help_text", lang), reply_markup=_support_kb(lang))


@router.callback_query(F.data == "support:start")
async def support_start(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.set_state(SupportForm.waiting)
    await callback.message.answer(i18n.t("support_prompt", lang))
    await callback.answer()


@router.message(SupportForm.waiting, F.text)
async def support_receive(message: Message, state: FSMContext, bot: Bot, lang: str) -> None:
    await state.clear()
    user = message.from_user
    uname = f" (@{escape(user.username)})" if user.username else ""
    admin_text = (
        "🆘 <b>Обращение в поддержку</b>\n"
        f"От: {escape(user.full_name or '')}{uname}, id=<code>{user.id}</code>\n\n"
        f"{escape(message.text)}\n\n"
        f"Ответить: <code>/reply {user.id} ваш текст</code>"
    )
    await notify_admins(bot, admin_text)
    await message.answer(i18n.t("support_sent", lang))


@router.message(Command("reply"))
async def cmd_reply(message: Message, session: AsyncSession, bot: Bot) -> None:
    """Ответ администратора пользователю: /reply <telegram_id> <текст>."""
    if not cfg().is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Формат: /reply &lt;id&gt; текст")
        return
    target_id = int(parts[1])
    target = await crud.get_user(session, target_id)
    target_lang = target.language if target and target.language else i18n.DEFAULT_LANG
    try:
        await bot.send_message(target_id, i18n.t("support_reply_delivered", target_lang, text=escape(parts[2])))
        await message.answer("✅ Ответ отправлен.")
    except Exception:  # noqa: BLE001
        await message.answer("⚠️ Не удалось доставить ответ (пользователь не запускал бота?).")


# ---------------------------------------------------------------------------
# Catch-all: первое (или случайное вне сценария) сообщение.
# Регистрируется ПОСЛЕДНИМ и исключает тексты кнопок меню.
# ---------------------------------------------------------------------------
@router.message(StateFilter(None), F.text & ~F.text.startswith("/") & ~F.text.in_(_MENU_TEXTS))
async def first_touch(message: Message, session: AsyncSession, lang: str) -> None:
    if cfg().is_admin(message.from_user.id):
        return
    user = await _ensure_user(message, session)
    if not user.language:
        await message.answer(_LANG_PROMPT, reply_markup=common_kb.language_kb())
    elif not user.role_chosen:
        await message.answer(i18n.t("choose_role", lang), reply_markup=common_kb.role_choice_kb(lang))
    else:
        await _open_menu(message, user.role, lang)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
async def _ensure_user(message: Message, session: AsyncSession) -> BotUser:
    return await crud.get_or_create_user(
        session,
        message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        is_admin=cfg().is_admin(message.from_user.id),
    )


async def _open_scenario(
    message: Message, state: FSMContext, role: UserRole, lang: str, full_name: str | None
) -> None:
    if role == UserRole.owner:
        await message.answer(i18n.t("owner_mode", lang), reply_markup=common_kb.owner_menu(lang))
        await begin_property_form(message, state, lang)
    else:
        await message.answer(i18n.t("client_mode", lang), reply_markup=common_kb.client_menu(lang))
        await begin_client_form(message, state, lang, full_name=full_name)


async def _demo_welcome(message: Message, user: BotUser | None, lang: str) -> None:
    """Демо: рассказать, что попробовать (кнопки ролей — в приветствии); меню — если роль уже есть."""
    await demo.send_welcome(message, lang)
    if user and user.role_chosen:
        await _open_menu(message, user.role, lang)


async def _open_menu(message: Message, role: UserRole, lang: str) -> None:
    if role == UserRole.owner:
        await message.answer(i18n.t("owner_menu_title", lang), reply_markup=common_kb.owner_menu(lang))
    else:
        await message.answer(i18n.t("client_menu_title", lang), reply_markup=common_kb.client_menu(lang))
