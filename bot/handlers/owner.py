"""Хендлеры собственника: размещение объекта одним сообщением (осн. путь).

Пользователь присылает объявление целиком — текст + фото/видео (часто
альбомом). Бот забирает ВСЕ медиа (фото и видео) из присланного, разбирает
поля ИИ-сервисом и оформляет описание. Вручную дозапрашивает только то, что
не распознал и что критично: тип сделки, цену, район/массив (из списка),
телефон. Адрес-ориентир («за рестораном», «рядом с домом») сохраняется как есть.
"""
from __future__ import annotations

import asyncio
import io
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.agency_config import cfg
from bot.config import get_settings
from bot.database import crud
from bot.database.models import (
    Property,
    PropertyKind,
    PropertyStatus,
    PropertyType,
    UserRole,
)
from bot.handlers import demo
from bot.keyboards import owner_kb
from bot.keyboards.admin_kb import moderation_kb
from bot.services import ai_quota, analytics, duplicate_checker
from bot.services.ai_service import get_ai_service
from bot.services.notify import notify_admins
from bot.services.transcribe import get_transcribe_service
from bot.services.vision import get_vision_service
from bot.states.owner_states import OwnerForm
from bot.utils import timeutils
from bot.utils.formatters import format_property_brief, format_property_card
from bot.utils.media import photo_input
from bot.utils.validators import is_valid_phone, parse_price

logger = logging.getLogger(__name__)
router = Router(name="owner")
P = owner_kb.PREFIX

# Сериализация записи медиа в FSM (альбом приходит конкурентными апдейтами).
_media_lock = asyncio.Lock()

# Поля из разбора объявления, переносимые в состояние
_LISTING_FIELDS = ("deal_type", "property_kind", "rooms", "district", "address",
                   "area", "floor", "floors", "currency", "description")

# Состояния, в которых по-прежнему принимаем медиа (фото/видео) из объявления/альбома
_MEDIA_STATES = (
    OwnerForm.raw, OwnerForm.deal_type, OwnerForm.price,
    OwnerForm.district, OwnerForm.phone, OwnerForm.photos, OwnerForm.confirm,
)


# ---------------------------------------------------------------------------
# Вход
# ---------------------------------------------------------------------------
async def begin_property_form(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    if cfg().readonly:  # подписка агентства не оплачена — объявления не принимаем
        await message.answer(i18n.t("readonly_owner", lang))
        return
    await state.set_state(OwnerForm.raw)
    await state.update_data(lang=lang)
    await message.answer(i18n.t("owner_intro", lang))


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext, session: AsyncSession, lang: str) -> None:
    await crud.get_or_create_user(
        session,
        message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        is_admin=cfg().is_admin(message.from_user.id),
    )
    if not cfg().is_admin(message.from_user.id):
        await crud.set_user_role(session, message.from_user.id, UserRole.owner)
    await begin_property_form(message, state, lang)


# ---------------------------------------------------------------------------
# Приём медиа (фото/видео) на любом шаге размещения — включая альбом с текстом
# ---------------------------------------------------------------------------
@router.message(StateFilter(*_MEDIA_STATES), F.photo | F.video)
async def collect_media(message: Message, state: FSMContext, lang: str) -> None:
    async with _media_lock:
        data = await state.get_data()
        photos: list[str] = list(data.get("photos", []))
        if message.photo:
            photos.append(message.photo[-1].file_id)
        update = {"photos": photos}
        if message.video:
            update["video"] = message.video.file_id
        mg = message.media_group_id
        first_in_group = not mg or data.get("_mg") != mg
        update["_mg"] = mg
        await state.update_data(**update)

    current = await state.get_state()
    caption = (message.caption or "").strip()

    if current == OwnerForm.raw.state:
        if caption and not data.get("_parsed"):
            # Фото/альбом с подписью → фото объекта, подпись = текст объявления
            await state.update_data(_parsed=True)
            await _handle_listing(message, state, lang, caption)
        elif (
            not caption and not mg and not data.get("_parsed")
            and not data.get("_vision_tried") and get_vision_service().is_enabled()
        ):
            # Одиночное фото без подписи в самом начале → скорее всего СКРИН
            # объявления: распознаём текст с картинки (Vision).
            await state.update_data(_vision_tried=True)
            await _handle_screenshot(message, state, lang)
        elif not caption and first_in_group and not data.get("_parsed") and not data.get("_asked_text"):
            await state.update_data(_asked_text=True)
            await message.answer(i18n.t("owner_send_text", lang))
    elif current == OwnerForm.photos.state and first_in_group:
        await message.answer(
            i18n.t("media_accepted", lang, n=len(photos)),
            reply_markup=owner_kb.photos_done_kb(lang),
        )
    # В остальных состояниях медиа тихо копится (стрелки альбома после разбора).


async def _download(bot: Bot, file_id: str) -> bytes:
    buf = io.BytesIO()
    await bot.download(file_id, destination=buf)
    return buf.getvalue()


async def _handle_screenshot(message: Message, state: FSMContext, lang: str) -> None:
    """Скрин объявления → Vision извлекает текст → обычный разбор. Картинку-скрин
    не оставляем как фото объекта."""
    if not await ai_quota.allow():                  # лимит ИИ по тарифу исчерпан — фото остаётся фото объекта
        await message.answer(i18n.t("owner_ai_limit", lang))
        return
    async with _media_lock:
        data = await state.get_data()
        screenshot_id = message.photo[-1].file_id
        photos = [p for p in data.get("photos", []) if p != screenshot_id]
        await state.update_data(photos=photos)

    await message.answer(i18n.t("owner_reading_image", lang))
    try:
        image = await _download(message.bot, message.photo[-1].file_id)
        text = await get_vision_service().extract_listing_text(image)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось скачать/распознать скрин")
        text = None

    if text:
        await state.update_data(_parsed=True)
        await _handle_listing(message, state, lang, text)
    else:
        # Не распознали — просим текст; фото не считаем объявлением
        await state.update_data(_vision_tried=False)
        await message.answer(i18n.t("owner_image_failed", lang))


@router.message(OwnerForm.raw, F.voice | F.audio)
async def listing_voice(message: Message, state: FSMContext, lang: str) -> None:
    """Голосовое объявление → транскрипция (Whisper) → обычный разбор."""
    data = await state.get_data()
    if data.get("_parsed"):
        return
    if not get_transcribe_service().is_enabled():
        await message.answer(i18n.t("owner_send_text", lang))
        return
    if not await ai_quota.allow():                  # лимит ИИ по тарифу исчерпан
        await message.answer(i18n.t("owner_ai_limit", lang))
        return
    await state.update_data(_parsed=True)
    await message.answer(i18n.t("owner_listening", lang))
    file_id = message.voice.file_id if message.voice else message.audio.file_id
    try:
        audio = await _download(message.bot, file_id)
        text = await get_transcribe_service().transcribe(audio)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось скачать/расшифровать голосовое")
        text = None

    if text:
        await _handle_listing(message, state, lang, text)
    else:
        await state.update_data(_parsed=False)
        await message.answer(i18n.t("owner_voice_failed", lang))


# ---------------------------------------------------------------------------
# Разбор объявления (текст)
# ---------------------------------------------------------------------------
@router.message(OwnerForm.raw, F.text)
async def listing_text(message: Message, state: FSMContext, lang: str) -> None:
    await state.update_data(_parsed=True)
    await _handle_listing(message, state, lang, message.text)


async def _handle_listing(message: Message, state: FSMContext, lang: str, text: str) -> None:
    # Мгновенная реакция: разбор через ИИ может занять пару секунд — бот не молчит.
    await message.answer(i18n.t("owner_parsing", lang))
    parsed = await get_ai_service().parse_listing(text)
    if parsed.get("rooms") and not parsed.get("property_kind"):
        parsed["property_kind"] = "apartment"

    update = {k: parsed[k] for k in _LISTING_FIELDS if parsed.get(k) is not None}
    if parsed.get("price") is not None:
        update["price"] = parsed["price"]
    if parsed.get("phone"):
        update["owner_phone"] = str(parsed["phone"])[:32]
    # Слияние под тем же локом, что и добавление медиа, — не теряем стрелки альбома.
    async with _media_lock:
        await state.update_data(**update)

    await message.answer(i18n.t("owner_quick_ok", lang))
    await _route_missing(message, state, lang)


# ---------------------------------------------------------------------------
# Дозапрос недостающего: сделка → цена → район/массив → телефон → медиа
# ---------------------------------------------------------------------------
async def _route_missing(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    if not data.get("deal_type"):
        await state.set_state(OwnerForm.deal_type)
        await message.answer(i18n.t("owner_need_deal", lang), reply_markup=owner_kb.deal_type_kb(lang))
        return
    if not data.get("property_kind"):
        await state.update_data(property_kind="apartment")  # тип по умолчанию
    if data.get("price") is None:
        await state.set_state(OwnerForm.price)
        await message.answer(i18n.t("owner_need_price", lang))
        return
    if not data.get("district"):
        await state.set_state(OwnerForm.district)
        await message.answer(i18n.t("owner_ask_massif", lang), reply_markup=owner_kb.district_kb(lang))
        return
    if not data.get("owner_phone"):
        await state.set_state(OwnerForm.phone)
        await message.answer(i18n.t("owner_ask_phone", lang))
        return
    await _ask_media(message, state, lang)


@router.callback_query(OwnerForm.deal_type, F.data.startswith(f"{P}:deal:"))
async def deal_cb(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.update_data(deal_type=callback.data.split(":")[2])
    await callback.message.edit_reply_markup(reply_markup=None)
    await _route_missing(callback.message, state, lang)
    await callback.answer()


@router.message(OwnerForm.price, F.text)
async def price_txt(message: Message, state: FSMContext, lang: str) -> None:
    value = parse_price(message.text)
    if value is None:
        await message.answer(i18n.t("price_bad", lang))
        return
    await state.update_data(price=value)
    await _route_missing(message, state, lang)


# --- Район/массив ---
@router.callback_query(OwnerForm.district, F.data.startswith(f"{P}:mass:"))
async def massif_cb(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    code = callback.data.split(":")[2]
    if code == "other":
        await callback.message.answer(i18n.t("owner_massif_type", lang))
        await callback.answer()
        return  # остаёмся в district, ждём текст
    if code != "skip":
        massif = owner_kb.get_massif(code)
        if massif:
            await state.update_data(district=massif)
    await callback.message.edit_reply_markup(reply_markup=None)
    # skip → district остаётся пустым (адрес-ориентир всё равно сохранён)
    if code == "skip":
        await state.update_data(district=None)
    await _route_missing(callback.message, state, lang)
    await callback.answer()


@router.message(OwnerForm.district, F.text)
async def massif_txt(message: Message, state: FSMContext, lang: str) -> None:
    await state.update_data(district=message.text.strip()[:128])
    await _route_missing(message, state, lang)


@router.message(OwnerForm.phone, F.text)
async def phone_txt(message: Message, state: FSMContext, lang: str) -> None:
    if not is_valid_phone(message.text):
        await message.answer(i18n.t("owner_phone_bad", lang))
        return
    await state.update_data(owner_phone=message.text.strip())
    await _route_missing(message, state, lang)


# ---------------------------------------------------------------------------
# Медиа-шаг (можно доложить фото/видео) → подтверждение
# ---------------------------------------------------------------------------
async def _ask_media(message: Message, state: FSMContext, lang: str) -> None:
    await state.set_state(OwnerForm.photos)
    await message.answer(i18n.t("ask_media", lang), reply_markup=owner_kb.photos_done_kb(lang))


@router.callback_query(OwnerForm.photos, F.data == f"{P}:pdone")
async def media_done(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await _show_confirm(callback.message, state, lang)
    await callback.answer()


# ---------------------------------------------------------------------------
# Подтверждение и сохранение
# ---------------------------------------------------------------------------
async def _show_confirm(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    preview = _build_preview(data)
    await state.set_state(OwnerForm.confirm)
    await message.answer(i18n.t("owner_confirm_title", lang))
    await message.answer(format_property_card(preview, lang), reply_markup=owner_kb.confirm_kb(lang))


@router.callback_query(OwnerForm.confirm, F.data == f"{P}:cancel")
async def confirm_cancel(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(i18n.t("owner_cancelled", lang))
    await callback.answer()


@router.callback_query(OwnerForm.confirm, F.data == f"{P}:confirm")
async def confirm_save(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot, lang: str
) -> None:
    data = await state.get_data()
    if get_settings().demo_mode and await _demo_limit_reached(session, callback.from_user.id):
        await _demo_limit_reply(callback, state, lang)
        return

    dups = await duplicate_checker.check_duplicates(session, data)
    if dups:
        brief = "\n".join("• " + format_property_brief(p) for p in dups)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            i18n.t("dup_warning", lang, brief=brief),
            reply_markup=owner_kb.dup_confirm_kb(lang),
        )
        await notify_admins(bot, "⚠️ Возможный дубль при добавлении объекта:\n" + brief)
        await callback.answer()
        return

    await _save_property(callback, state, session, bot, lang)


@router.callback_query(OwnerForm.confirm, F.data == f"{P}:dupyes")
async def dup_yes(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot, lang: str) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await _save_property(callback, state, session, bot, lang)


@router.callback_query(OwnerForm.confirm, F.data == f"{P}:dupno")
async def dup_no(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(i18n.t("dup_cancelled", lang))
    await callback.answer()


async def _save_property(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot, lang: str
) -> None:
    data = await state.get_data()
    settings = get_settings()
    if cfg().readonly:  # подписка кончилась, пока человек заполнял форму
        await state.clear()
        await callback.message.answer(i18n.t("readonly_owner", lang))
        await callback.answer()
        return
    if settings.demo_mode and await _demo_limit_reached(session, callback.from_user.id):
        await _demo_limit_reply(callback, state, lang)  # страховка для пути «всё равно добавить» (дубль)
        return

    description = data.get("description") or await get_ai_service().generate_short_description(data)

    photos = data.get("photos", [])
    if settings.demo_mode:
        photos = photos[:1]  # одно фото = один пост: ночной сброс удалит его из канала целиком
    prop = await crud.create_property(
        session,
        property_kind=PropertyKind(data.get("property_kind", "apartment")),
        type=PropertyType(data["deal_type"]),
        status=PropertyStatus.pending,
        owner_name=callback.from_user.full_name,
        owner_phone=data.get("owner_phone"),
        owner_telegram_id=callback.from_user.id,
        district=data.get("district"),
        address=data.get("address"),
        rooms=data.get("rooms"),
        area=data.get("area"),
        floor=data.get("floor"),
        floors=data.get("floors"),
        renovation=data.get("renovation"),
        price=data.get("price"),
        currency=data.get("currency") or "сум",
        description=description,
        photos=",".join(photos),
        video=data.get("video"),
    )
    await state.clear()
    await analytics.track("listing", telegram_id=callback.from_user.id, property_id=prop.id)

    await callback.message.edit_reply_markup(reply_markup=None)
    if settings.demo_mode:
        await callback.answer()
        await demo.publish_visitor_listing(callback.message, session, bot, lang, prop, callback.from_user)
        return
    await callback.message.answer(i18n.t("sent_to_moderation", lang, id=prop.id))
    await callback.answer()

    await _notify_admins(bot, prop)


async def _demo_limit_reply(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(i18n.t("demo_limit", lang, n=get_settings().demo_daily_limit))
    await callback.answer()


async def _demo_limit_reached(session: AsyncSession, telegram_id: int) -> bool:
    today = timeutils.now().replace(hour=0, minute=0, second=0, microsecond=0)
    count = await crud.count_owner_properties_since(session, telegram_id, today)
    return count >= get_settings().demo_daily_limit


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def _build_preview(data: dict) -> Property:
    return Property(
        id="—",
        type=PropertyType(data["deal_type"]),
        property_kind=PropertyKind(data.get("property_kind", "apartment")),
        owner_phone=data.get("owner_phone"),
        district=data.get("district"),
        address=data.get("address"),
        rooms=data.get("rooms"),
        area=data.get("area"),
        floor=data.get("floor"),
        floors=data.get("floors"),
        renovation=data.get("renovation"),
        price=data.get("price"),
        currency=data.get("currency") or "сум",
        description=data.get("description"),
    )


async def _notify_admins(bot: Bot, prop: Property) -> None:
    text = "🆕 <b>Объект на модерацию</b>\n\n" + format_property_card(prop)
    kb = moderation_kb(prop.id)
    photos = prop.photo_list
    for admin_id in cfg().admin_ids:
        try:
            if photos:
                await bot.send_photo(admin_id, photo=photo_input(photos[0]), caption=text[:1024], reply_markup=kb)
            else:
                await bot.send_message(admin_id, text, reply_markup=kb)
        except Exception:  # noqa: BLE001 - админ мог не запускать бота
            logger.debug("Не удалось уведомить админа %s", admin_id)
