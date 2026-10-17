"""Хендлеры администратора: объекты, клиенты, встречи, публикации, статистика.

Мультиязычно: язык берётся из middleware (bot_users.language). Админ выбирает
язык при первом входе и кнопкой «🌐 Язык». Уведомления, отправляемые из других
модулей (модерация нового объекта), остаются на русском — единый канонический
текст для рассылки нескольким админам.
"""
from __future__ import annotations

import datetime as dt
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.config import BUMP_INTERVAL_DAYS, get_settings
from bot.database import crud
from bot.database.models import (
    BotUser,
    ClientStatus,
    MeetingStatus,
    Property,
    PropertyStatus,
)
from bot.handlers.owner import begin_property_form
from bot.keyboards import admin_kb
from bot.services import backups, export, matcher, publisher
from bot.services.ai_service import get_ai_service
from bot.services.transcribe import get_transcribe_service
from bot.services.vision import get_vision_service
from bot.states.admin_states import EditProperty, ScheduleMeeting
from bot.utils import timeutils
from bot.utils.formatters import (
    format_client_short,
    format_meeting,
    format_property_card,
    property_status_label,
)
from bot.utils.media import photo_input
from bot.utils.validators import parse_int, parse_price

logger = logging.getLogger(__name__)
P = admin_kb.PREFIX

# Подписи reply-меню админа на всех языках (матчинг по тексту)
BTN_NEW = i18n.btn_variants("adm_new")
BTN_OBJECTS = i18n.btn_variants("adm_objects")
BTN_CLIENTS = i18n.btn_variants("adm_clients")
BTN_MEETINGS = i18n.btn_variants("adm_meetings")
BTN_PUBLICATIONS = i18n.btn_variants("adm_publications")
BTN_STATS = i18n.btn_variants("adm_stats")
BTN_LANGUAGE = i18n.btn_variants("language")


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return bool(user and get_settings().is_admin(user.id))


router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_STATUS_FILTER = {
    "all": None,
    "active": [PropertyStatus.active],
    "pending": [PropertyStatus.pending],
    "sold": [PropertyStatus.rented, PropertyStatus.sold],
}

PAGE = 5  # объектов/клиентов на страницу


# ---------------------------------------------------------------------------
# Меню и язык
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession, lang: str) -> None:
    await state.clear()
    user = await crud.get_or_create_user(
        session,
        message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        is_admin=True,
    )
    if not user.language:
        await message.answer(
            "🌐 Tilni tanlang / Выберите язык / Choose your language:",
            reply_markup=admin_kb.language_kb(),
        )
        return
    await message.answer(i18n.t("adm_panel_title", lang), reply_markup=admin_kb.main_menu(lang))


@router.message(F.text.in_(BTN_LANGUAGE))
async def admin_language(message: Message) -> None:
    await message.answer(
        "🌐 Tilni tanlang / Выберите язык / Choose your language:",
        reply_markup=admin_kb.language_kb(),
    )


@router.callback_query(F.data.startswith("alang:"))
async def admin_set_language(callback: CallbackQuery, session: AsyncSession) -> None:
    code = i18n.normalize(callback.data.split(":")[1])
    await crud.set_language(session, callback.from_user.id, code)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await callback.message.answer(i18n.t("language_saved", code, name=i18n.LANGUAGES[code]))
    await callback.message.answer(i18n.t("adm_panel_title", code), reply_markup=admin_kb.main_menu(code))


@router.message(F.text.in_(BTN_NEW))
async def new_object(message: Message, state: FSMContext, lang: str) -> None:
    await begin_property_form(message, state, lang)


# ---------------------------------------------------------------------------
# Раздел: Объекты
# ---------------------------------------------------------------------------
@router.message(F.text.in_(BTN_OBJECTS))
async def objects_menu(message: Message, lang: str) -> None:
    await message.answer(i18n.t("adm_objects_filter", lang), reply_markup=admin_kb.object_filters_kb(lang))


@router.callback_query(F.data.startswith(f"{P}:objs:"))
async def objects_list(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    parts = callback.data.split(":")
    key = parts[2]
    offset = int(parts[3]) if len(parts) > 3 else 0
    props = await crud.list_properties(session, statuses=_STATUS_FILTER.get(key), limit=PAGE, offset=offset)
    await callback.answer()
    if not props:
        await callback.message.answer(i18n.t("adm_no_more", lang) if offset else i18n.t("adm_nothing_found", lang))
        return
    for prop in props:
        await _show_property(callback.message, prop, admin_kb.object_actions_kb(prop, lang), lang)
    if len(props) == PAGE:
        await callback.message.answer("…", reply_markup=admin_kb.more_kb(f"{P}:objs:{key}:{offset + PAGE}", lang))


async def _show_property(target: Message, prop: Property, kb, lang: str) -> None:
    status = property_status_label(prop.status, lang)
    text = f"[{status}] " + format_property_card(prop, lang)
    photos = prop.photo_list
    if photos:
        try:
            await target.answer_photo(photo_input(photos[0]), caption=text[:1024], reply_markup=kb)
            return
        except Exception:  # noqa: BLE001 - битое/удалённое фото не должно ломать список
            logger.warning("Фото объекта %s не отправилось — показываю без фото", prop.id)
    await target.answer(text, reply_markup=kb)


# --- действия с объектом ---
@router.callback_query(F.data.startswith(f"{P}:approve:"))
async def approve(callback: CallbackQuery, session: AsyncSession, bot: Bot, lang: str) -> None:
    property_id = callback.data.split(":")[2]
    try:
        prop = await publisher.publish_property(bot, session, property_id)
    except Exception:  # noqa: BLE001 - ошибка отправки в канал не должна «вешать» callback
        logger.exception("Ошибка публикации объекта %s", property_id)
        await callback.answer(i18n.t("adm_publish_error", lang), show_alert=True)
        return
    if prop is None:
        await callback.answer(i18n.t("adm_not_found", lang), show_alert=True)
        return
    await _clear_markup(callback)
    await callback.answer(i18n.t("adm_t_published", lang), show_alert=True)
    await _notify_owner(bot, session, prop, "adm_published_owner", id=prop.id)


@router.callback_query(F.data.startswith(f"{P}:hold:"))
async def hold(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    await crud.set_property_status(session, callback.data.split(":")[2], PropertyStatus.pending)
    await _clear_markup(callback)
    await callback.answer(i18n.t("adm_t_hold", lang), show_alert=True)


@router.callback_query(F.data.startswith(f"{P}:sold:"))
async def sold(callback: CallbackQuery, session: AsyncSession, bot: Bot, lang: str) -> None:
    await publisher.mark_as_rented(bot, session, callback.data.split(":")[2])
    await _clear_markup(callback)
    await callback.answer(i18n.t("adm_t_sold", lang), show_alert=True)


@router.callback_query(F.data.startswith(f"{P}:bump:"))
async def bump(callback: CallbackQuery, session: AsyncSession, bot: Bot, lang: str) -> None:
    await publisher.bump_property(bot, session, callback.data.split(":")[2])
    await callback.answer(i18n.t("adm_t_bumped", lang), show_alert=True)


@router.callback_query(F.data.startswith(f"{P}:arch:"))
async def archive(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    await crud.set_property_status(session, callback.data.split(":")[2], PropertyStatus.archived)
    await _clear_markup(callback)
    await callback.answer(i18n.t("adm_t_archived", lang), show_alert=True)


# --- редактирование объекта ---
@router.callback_query(F.data.startswith(f"{P}:edit:"))
async def edit_start(callback: CallbackQuery, lang: str) -> None:
    property_id = callback.data.split(":")[2]
    await callback.message.answer(
        i18n.t("adm_edit_title", lang, id=property_id),
        reply_markup=admin_kb.edit_fields_kb(property_id, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{P}:efield:"))
async def edit_field(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    _, _, property_id, field = callback.data.split(":")
    await state.update_data(property_id=property_id, field=field)
    await state.set_state(EditProperty.value)
    await callback.message.answer(i18n.t("adm_edit_prompt", lang, field=admin_kb.edit_field_label(field, lang)))
    await callback.answer()


@router.message(EditProperty.value, F.text)
async def edit_value(message: Message, state: FSMContext, session: AsyncSession, lang: str) -> None:
    data = await state.get_data()
    field, property_id = data["field"], data["property_id"]
    prop = await crud.get_property(session, property_id)
    if prop is None:
        await state.clear()
        await message.answer(i18n.t("adm_obj_not_found", lang))
        return

    if field in ("price", "area"):
        value = parse_price(message.text)
        if value is None:
            await message.answer(i18n.t("adm_enter_number", lang))
            return
    elif field == "rooms":
        value = parse_int(message.text)
        if value is None:
            await message.answer(i18n.t("adm_enter_int", lang))
            return
    else:
        value = message.text.strip()

    setattr(prop, field, value)
    await session.commit()
    await state.clear()
    await message.answer(i18n.t("adm_field_updated", lang, field=admin_kb.edit_field_label(field, lang)))


# ---------------------------------------------------------------------------
# Раздел: Клиенты
# ---------------------------------------------------------------------------
@router.message(F.text.in_(BTN_CLIENTS))
async def clients_list(message: Message, session: AsyncSession, lang: str) -> None:
    await _show_clients_page(message, session, lang, offset=0)


@router.callback_query(F.data.startswith(f"{P}:cli:"))
async def clients_more(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    await callback.answer()
    await _show_clients_page(callback.message, session, lang, offset=int(callback.data.split(":")[2]))


async def _show_clients_page(target: Message, session: AsyncSession, lang: str, *, offset: int) -> None:
    clients = await crud.list_clients(session, limit=PAGE, offset=offset)
    if not clients:
        await target.answer(i18n.t("adm_no_more_clients", lang) if offset else i18n.t("adm_no_clients", lang))
        return
    for client in clients:
        await target.answer(format_client_short(client, lang), reply_markup=admin_kb.client_actions_kb(client, lang))
    if len(clients) == PAGE:
        await target.answer("…", reply_markup=admin_kb.more_kb(f"{P}:cli:{offset + PAGE}", lang))


@router.callback_query(F.data.startswith(f"{P}:cstatus:"))
async def change_client_status(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    _, _, client_id, status = callback.data.split(":")
    client = await crud.set_client_status(session, client_id, ClientStatus(status))
    if client:
        await callback.message.edit_text(
            format_client_short(client, lang), reply_markup=admin_kb.client_actions_kb(client, lang)
        )
    await callback.answer(i18n.t("adm_status_updated", lang))


@router.callback_query(F.data.startswith(f"{P}:cmatch:"))
async def client_matches(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    client = await crud.get_client(session, callback.data.split(":")[2])
    await callback.answer()
    if client is None:
        return
    props = await matcher.find_for_client(session, client)
    if not props:
        await callback.message.answer(i18n.t("adm_no_matches", lang))
        return
    await callback.message.answer(i18n.t("adm_client_matches", lang, id=client.id))
    for prop in props:
        await callback.message.answer(format_property_card(prop, lang))


# ---------------------------------------------------------------------------
# Раздел: Встречи
# ---------------------------------------------------------------------------
@router.message(F.text.in_(BTN_MEETINGS))
async def meetings_list(message: Message, session: AsyncSession, lang: str) -> None:
    meetings = await crud.upcoming_meetings(session)
    if meetings:
        for meeting in meetings:
            await message.answer(format_meeting(meeting, lang), reply_markup=admin_kb.meeting_actions_kb(meeting.id, lang))
    else:
        await message.answer(i18n.t("adm_no_meetings", lang))
    await message.answer(i18n.t("adm_meetings_manage", lang), reply_markup=admin_kb.meetings_menu_kb(lang))


@router.callback_query(F.data.startswith(f"{P}:mdone:"))
async def meeting_done(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    meeting = await crud.set_meeting_status(session, callback.data.split(":")[2], MeetingStatus.done)
    if meeting:
        await crud.set_client_status(session, meeting.client_id, ClientStatus.showing_done)
        await _clear_markup(callback)
    await callback.answer(i18n.t("adm_t_meeting_done", lang), show_alert=True)


@router.callback_query(F.data.startswith(f"{P}:mcancel:"))
async def meeting_cancel(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    await crud.set_meeting_status(session, callback.data.split(":")[2], MeetingStatus.cancelled)
    await _clear_markup(callback)
    await callback.answer(i18n.t("adm_t_meeting_cancel", lang), show_alert=True)


@router.callback_query(F.data == f"{P}:meet_new")
async def meeting_new(callback: CallbackQuery, state: FSMContext, session: AsyncSession, lang: str) -> None:
    clients = await crud.list_clients(session, limit=10)
    if not clients:
        await callback.answer(i18n.t("adm_no_clients_alert", lang), show_alert=True)
        return
    await state.set_state(ScheduleMeeting.client)
    await callback.message.answer(i18n.t("adm_pick_client", lang), reply_markup=admin_kb.pick_clients_kb(clients))
    await callback.answer()


@router.callback_query(ScheduleMeeting.client, F.data.startswith(f"{P}:mclient:"))
async def meeting_pick_client(callback: CallbackQuery, state: FSMContext, session: AsyncSession, lang: str) -> None:
    await state.update_data(client_id=callback.data.split(":")[2])
    props = await crud.list_properties(session, statuses=[PropertyStatus.active], limit=10)
    if not props:
        await state.clear()
        await callback.answer(i18n.t("adm_no_active", lang), show_alert=True)
        return
    await state.set_state(ScheduleMeeting.property)
    await callback.message.edit_text(i18n.t("adm_pick_object", lang), reply_markup=admin_kb.pick_properties_kb(props))
    await callback.answer()


@router.callback_query(ScheduleMeeting.property, F.data.startswith(f"{P}:mprop:"))
async def meeting_pick_property(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.update_data(property_id=callback.data.split(":")[2])
    await state.set_state(ScheduleMeeting.date)
    await callback.message.edit_text(i18n.t("adm_meeting_date", lang))
    await callback.answer()


@router.message(ScheduleMeeting.date, F.text)
async def meeting_date(message: Message, state: FSMContext, lang: str) -> None:
    from bot.utils.validators import parse_date

    date = parse_date(message.text)
    if date is None:
        await message.answer(i18n.t("adm_bad_date", lang))
        return
    await state.update_data(date=date.isoformat())
    await state.set_state(ScheduleMeeting.time)
    await message.answer(i18n.t("adm_meeting_time", lang))


@router.message(ScheduleMeeting.time, F.text)
async def meeting_time(message: Message, state: FSMContext, session: AsyncSession, lang: str) -> None:
    try:
        time = dt.datetime.strptime(message.text.strip(), "%H:%M").time()
    except ValueError:
        await message.answer(i18n.t("adm_bad_time", lang))
        return
    data = await state.get_data()
    when = dt.datetime.combine(dt.date.fromisoformat(data["date"]), time)
    meeting = await crud.create_meeting(
        session, client_id=data["client_id"], property_id=data["property_id"], when=when
    )
    await crud.set_client_status(session, data["client_id"], ClientStatus.showing_set)
    await state.clear()
    await message.answer(i18n.t("adm_meeting_created", lang, id=meeting.id, when=when.strftime("%d.%m.%Y %H:%M")))


# ---------------------------------------------------------------------------
# Раздел: Публикации
# ---------------------------------------------------------------------------
@router.message(F.text.in_(BTN_PUBLICATIONS))
async def publications(message: Message, session: AsyncSession, lang: str) -> None:
    pending = await crud.list_properties(session, statuses=[PropertyStatus.pending])
    active = await crud.list_properties(session, statuses=[PropertyStatus.active])

    if pending:
        await message.answer(i18n.t("adm_pub_queue", lang))
        for prop in pending:
            await _show_property(message, prop, admin_kb.object_actions_kb(prop, lang), lang)
    else:
        await message.answer(i18n.t("adm_pub_queue_empty", lang))

    if active:
        await message.answer(i18n.t("adm_pub_published", lang))
        for prop in active:
            await message.answer(format_property_card(prop, lang), reply_markup=admin_kb.bump_kb(prop.id, lang))


# ---------------------------------------------------------------------------
# Раздел: Статистика
# ---------------------------------------------------------------------------
@router.message(F.text.in_(BTN_STATS))
async def statistics(message: Message, session: AsyncSession, lang: str) -> None:
    prop_stats = await crud.count_properties_by_status(session)
    client_stats = await crud.count_clients_by_status(session)

    threshold = timeutils.now() - dt.timedelta(days=BUMP_INTERVAL_DAYS)
    due = await crud.properties_due_for_bump(session, threshold)

    total = sum(prop_stats.values())
    lines = [
        i18n.t("st_title", lang),
        "",
        f"{i18n.t('st_total', lang)}: {total}",
        f"  • {i18n.t('st_active', lang)}: {prop_stats.get('active', 0)}",
        f"  • {i18n.t('st_pending', lang)}: {prop_stats.get('pending', 0)}",
        f"  • {i18n.t('st_sold_rented', lang)}: {prop_stats.get('rented', 0)} · {i18n.t('st_sold', lang)}: {prop_stats.get('sold', 0)}",
        f"  • {i18n.t('st_archive', lang)}: {prop_stats.get('archived', 0)}",
        "",
        i18n.t("st_funnel", lang),
        f"  • {i18n.t('cst_new', lang)}: {client_stats.get('new', 0)}",
        f"  • {i18n.t('cst_contacted', lang)}: {client_stats.get('contacted', 0)}",
        f"  • {i18n.t('cst_showing_set', lang)}: {client_stats.get('showing_set', 0)}",
        f"  • {i18n.t('cst_showing_done', lang)}: {client_stats.get('showing_done', 0)}",
        f"  • {i18n.t('cst_deal', lang)}: {client_stats.get('deal', 0)}",
        f"  • {i18n.t('cst_closed', lang)}: {client_stats.get('closed', 0)}",
        "",
        f"{i18n.t('st_need_bump', lang)}: {len(due)}",
    ]
    await message.answer("\n".join(lines), reply_markup=admin_kb.export_kb(lang))


@router.callback_query(F.data.startswith(f"{P}:export:"))
async def export_data(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    _, _, what, fmt = callback.data.split(":")
    if what == "props":
        data, filename = await export.export_properties(session, fmt)
    else:
        data, filename = await export.export_clients(session, fmt)
    await callback.message.answer_document(BufferedInputFile(data, filename=filename))
    await callback.answer(i18n.t("adm_export_done", lang))


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
async def _clear_markup(callback: CallbackQuery) -> None:
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - сообщение могло быть без клавиатуры
        pass


async def _notify_owner(bot: Bot, session: AsyncSession, prop: Property, msg_key: str, **kwargs) -> None:
    if not prop.owner_telegram_id:
        return
    owner = await session.get(BotUser, prop.owner_telegram_id)
    owner_lang = owner.language if owner and owner.language else i18n.DEFAULT_LANG
    try:
        await bot.send_message(prop.owner_telegram_id, i18n.t(msg_key, owner_lang, **kwargs))
    except Exception:  # noqa: BLE001 - собственник мог заблокировать бота
        logger.debug("Не удалось уведомить собственника %s", prop.owner_telegram_id)


# ---------------------------------------------------------------------------
# Диагностика ИИ-провайдеров (чтобы снятие модели не стоило дней поиска)
# ---------------------------------------------------------------------------
@router.message(Command("ai_status"))
async def ai_status(message: Message) -> None:
    """Живой тест каждого провайдера цепочки: текст, Vision, голос."""
    await message.answer("🔎 Проверяю ИИ-провайдеров…")
    lines = ["<b>🧠 Текст (разбор объявлений)</b>"]
    text_router = get_ai_service().router
    if not text_router.enabled:
        lines.append("❌ не настроен — работает офлайн-разбор (регулярки)")
    for r in await text_router.probe():
        mark = "✅" if r["ok"] else "❌"
        tail = f" — {r['ms']} мс" if r["ok"] else f" — {escape(r['error'])}"
        lines.append(f"{mark} {r['name']}: <code>{escape(r['model'])}</code>{tail}")

    lines.append("\n<b>🖼 Vision (скрины)</b>")
    vis = get_vision_service().router
    if not vis.enabled:
        lines.append("— выключено (нет GEMINI_API_KEY)")
    for r in await vis.probe():
        mark = "✅" if r["ok"] else "❌"
        tail = f" — {r['ms']} мс" if r["ok"] else f" — {escape(r['error'])}"
        lines.append(f"{mark} {r['name']}: <code>{escape(r['model'])}</code>{tail}")

    stt = get_transcribe_service()
    lines.append("\n<b>🎧 Голос</b>")
    lines.append("✅ включён" if stt.is_enabled() else "— выключен (нет ELEVENLABS_API_KEY / GROQ_API_KEY)")
    lines.append("\nЕсли модель ❌ «not found» — посмотри актуальные: /ai_models groq | gemini | openrouter")
    await message.answer("\n".join(lines))


@router.message(Command("ai_models"))
async def ai_models(message: Message, command: CommandObject) -> None:
    """Список реально доступных моделей провайдера — не угадываем имена."""
    name = (command.args or "").strip().lower()
    if not name:
        await message.answer("Использование: /ai_models groq | gemini | openrouter | deepseek | cerebras | mistral")
        return
    try:
        models = await get_ai_service().router.list_models(name)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"❌ {escape(str(exc)[:300])}")
        return
    chunk = "\n".join(models[:80])
    more = f"\n… и ещё {len(models) - 80}" if len(models) > 80 else ""
    await message.answer(f"<b>{escape(name)}</b> — {len(models)} моделей:\n<code>{escape(chunk)}</code>{more}")


# ---------------------------------------------------------------------------
# Бэкапы базы
# ---------------------------------------------------------------------------
@router.message(Command("backup"))
async def backup_now(message: Message, bot: Bot) -> None:
    """Свежий бэкап базы — файлом в этот чат (проверка, что бэкапы живы)."""
    result = await backups.ship_latest_backup(bot, chat_id=message.chat.id, force=True)
    if result == "disabled":
        await message.answer(
            "Бэкапы не подключены: в контейнере нет папки с дампами.\n"
            "Обнови сервер: <code>git pull && docker compose up -d --build</code> — "
            "поднимется сервис <code>backup</code>."
        )
    # sent → файл уже в чате; error / stale / too_big → админам ушло предупреждение
