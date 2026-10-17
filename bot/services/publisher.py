"""Публикация объектов в канал и управление постами.

Функции принимают property_id, берут объект из БД, формируют текст объявления
через AI-сервис (строгий формат, MarkdownV2) и работают с каналом.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from sqlalchemy.ext.asyncio import AsyncSession

from bot.agency_config import cfg
from bot.database import crud
from bot.database.models import Property, PropertyStatus, PropertyType
from bot.services import analytics
from bot.services.ai_service import get_ai_service
from bot.services.watermark import watermark_photos
from bot.utils import timeutils

logger = logging.getLogger(__name__)


def _prop_to_dict(prop: Property) -> dict:
    return {
        "deal_type": prop.type.value if prop.type else None,
        "property_kind": prop.property_kind.value if prop.property_kind else None,
        "address": prop.address,
        "district": prop.district,
        "rooms": prop.rooms,
        "area": prop.area,
        "floor": prop.floor,
        "floors": prop.floors,
        "renovation": prop.renovation,
        "gas": prop.gas,
        "water": prop.water,
        "electricity": prop.electricity,
        "internet": prop.internet,
        "price": prop.price,
        "currency": prop.currency,
        "negotiable": prop.negotiable,
    }


CAPTION_LIMIT = 1024  # лимит подписи к фото в Telegram
MAX_ALBUM = 10        # лимит фото в одной медиагруппе

# Подпись кнопки-заявки под постом (канал узбекоязычный + дубль на русском)
LEAD_BUTTON_TEXT = "✍️ Ariza qoldirish · Оставить заявку"


async def _lead_kb(bot: Bot, property_id: str) -> InlineKeyboardMarkup | None:
    """Инлайн-кнопка «Оставить заявку» → deep-link в бота с id объекта."""
    try:
        username = (await bot.me()).username
    except Exception:  # noqa: BLE001 - на всякий случай, без кнопки пост всё равно уйдёт
        return None
    if not username:
        return None
    url = f"https://t.me/{username}?start=lead_{property_id}"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=LEAD_BUTTON_TEXT, url=url)]])


def _caption_post(photos: list[str], text: str) -> bool:
    """Публикуется ли объект как фото-с-подписью (иначе — отдельным текстом)."""
    return bool(photos) and len(text) <= CAPTION_LIMIT


async def _send_album(bot: Bot, channel_id: str, photos: list, caption: str | None) -> int | None:
    """Отправить фото альбомами по MAX_ALBUM штук (Telegram не примет >10 разом).

    Подпись ставится на первое фото первого альбома. Возвращает id первого
    сообщения (для последующего редактирования подписи)."""
    first_id: int | None = None
    for start in range(0, len(photos), MAX_ALBUM):
        chunk = photos[start : start + MAX_ALBUM]
        media = [
            InputMediaPhoto(
                media=fid,
                caption=caption if (start == 0 and i == 0 and caption) else None,
                parse_mode=ParseMode.MARKDOWN_V2 if (start == 0 and i == 0 and caption) else None,
            )
            for i, fid in enumerate(chunk)
        ]
        messages = await bot.send_media_group(channel_id, media)
        if first_id is None:
            first_id = messages[0].message_id
    return first_id


async def _send_post(
    bot: Bot, channel_id: str, text: str, photos: list, reply_markup=None
) -> int:
    """Отправить пост и вернуть id сообщения, несущего описание (для будущих правок).

    Инлайн-кнопки (заявка) нельзя прикрепить к медиагруппе, поэтому:
    - одно фото + короткий текст → фото с подписью и кнопкой (id = фото);
    - несколько фото / длинный текст → медиа без подписи + отдельное текстовое
      сообщение с описанием и кнопкой (id = текстовое сообщение);
    - без фото → текстовое сообщение с кнопкой.
    Альбомы бьются на пачки по 10 фото.
    """
    if len(photos) == 1 and len(text) <= CAPTION_LIMIT:
        message = await bot.send_photo(
            channel_id, photos[0], caption=text,
            parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup,
        )
        return message.message_id

    if photos:  # альбом ИЛИ длинный текст → медиа без подписи + текст с кнопкой
        if len(photos) == 1:
            await bot.send_photo(channel_id, photos[0])
        else:
            await _send_album(bot, channel_id, photos, caption=None)

    message = await bot.send_message(
        channel_id, text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
    )
    return message.message_id


async def _edit_post(bot: Bot, channel_id: str, message_id: int, text: str, is_caption: bool) -> None:
    async def _try_caption() -> None:
        await bot.edit_message_caption(
            chat_id=channel_id, message_id=message_id, caption=text, parse_mode=ParseMode.MARKDOWN_V2
        )

    async def _try_text() -> None:
        await bot.edit_message_text(
            text, chat_id=channel_id, message_id=message_id, parse_mode=ParseMode.MARKDOWN_V2
        )

    order = (_try_caption, _try_text) if is_caption else (_try_text, _try_caption)
    for attempt in order:
        try:
            await attempt()
            return
        except Exception:  # noqa: BLE001 - пробуем альтернативный способ / пост изменён вручную
            continue
    logger.debug("Не удалось отредактировать пост %s", message_id)


async def publish_property(bot: Bot, session: AsyncSession, property_id: str) -> Property | None:
    """Опубликовать объект в канал (с фото), сохранить post_id, обновить last_bump."""
    prop = await crud.get_property(session, property_id)
    if prop is None:
        return None

    text = get_ai_service().generate_description(_prop_to_dict(prop))
    kb = await _lead_kb(bot, prop.id)
    media = await watermark_photos(bot, prop.photo_list)
    post_id = await _send_post(bot, cfg().channel_id, text, media, reply_markup=kb)

    prop.channel_post_id = post_id
    prop.status = PropertyStatus.active
    prop.last_bump = timeutils.now()
    await session.commit()
    logger.info("Объект %s опубликован (post=%s)", prop.id, post_id)
    await analytics.track("published", property_id=prop.id)

    await _notify_matching_clients(bot, session, prop)
    return prop


async def _notify_matching_clients(bot: Bot, session: AsyncSession, prop: Property) -> None:
    """Подписки на поиск: прислать новый объект подходящим клиентам (bot/services/alerts.py)."""
    from bot.services.alerts import send_property_alerts

    await send_property_alerts(bot, session, prop)


async def mark_as_rented(bot: Bot, session: AsyncSession, property_id: str) -> Property | None:
    """Отметить объект сданным/проданным: пометить пост и сменить статус."""
    prop = await crud.get_property(session, property_id)
    if prop is None:
        return None

    if prop.type == PropertyType.sale:
        prop.status, label = PropertyStatus.sold, "ПРОДАНО"
    else:
        prop.status, label = PropertyStatus.rented, "СДАНО"

    if prop.channel_post_id:
        base = get_ai_service().generate_description(_prop_to_dict(prop))
        text = f"⛔ *{label}*\n\n" + base
        await _edit_post(
            bot, cfg().channel_id, prop.channel_post_id, text,
            is_caption=_caption_post(prop.photo_list, base),
        )

    await session.commit()
    logger.info("Объект %s отмечен как %s", prop.id, label)
    return prop


async def archive_stale(bot: Bot, session: AsyncSession, property_id: str) -> Property | None:
    """Снять неподтверждённый объект: архив + пометка поста «эълон долзарб эмас»."""
    prop = await crud.get_property(session, property_id)
    if prop is None:
        return None
    prop.status = PropertyStatus.archived
    if prop.channel_post_id:
        base = get_ai_service().generate_description(_prop_to_dict(prop))
        await _edit_post(
            bot, cfg().channel_id, prop.channel_post_id, "🗄 *Эълон долзарб эмас*\n\n" + base,
            is_caption=_caption_post(prop.photo_list, base),
        )
    await session.commit()
    logger.info("Объект %s снят: нет ответа об актуальности", prop.id)
    return prop


async def bump_property(bot: Bot, session: AsyncSession, property_id: str) -> Property | None:
    """Поднять объект: пометить старый пост архивным и опубликовать новый."""
    prop = await crud.get_property(session, property_id)
    if prop is None:
        return None

    channel_id = cfg().channel_id
    text = get_ai_service().generate_description(_prop_to_dict(prop))

    # Помечаем старый пост как неактуальный (архив)
    if prop.channel_post_id:
        archived_text = "🗄 *Эълон янгиланди \\(пастга қаранг\\)*\n\n" + text
        await _edit_post(
            bot, channel_id, prop.channel_post_id, archived_text,
            is_caption=_caption_post(prop.photo_list, text),
        )

    # Публикуем свежий пост
    kb = await _lead_kb(bot, prop.id)
    media = await watermark_photos(bot, prop.photo_list)
    post_id = await _send_post(bot, channel_id, text, media, reply_markup=kb)
    prop.channel_post_id = post_id
    prop.last_bump = timeutils.now()
    await session.commit()
    logger.info("Объект %s поднят (новый post=%s)", prop.id, post_id)
    return prop
