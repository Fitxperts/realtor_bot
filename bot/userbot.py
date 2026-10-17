"""Авто-репост из чужих каналов (юзербот на Telethon) — задача 7.

Отдельный процесс: слушает каналы партнёров/конкурентов реальным аккаунтом
(бот не может читать чужие каналы), фильтрует объявления, разбирает их ИИ,
переделывает под шаблон агентства и:
  - AUTOREPOST_MODE=moderate → создаёт объект в статусе «на модерации» и шлёт
    админам карточку с кнопками (одобрить/редактировать/отклонить);
  - AUTOREPOST_MODE=auto → сразу публикует в канал.

Дедуп: по нормализованному тексту (Redis), плюс проверка на дубли в БД.
Фото скачиваются на общий том (MEDIA_DIR), доступный и боту (для публикации).

⚠️ Юзерботы формально против ToS Telegram — используйте ОТДЕЛЬНЫЙ номер, не
личный. Репост чужого контента — на ваше усмотрение и ответственность.

Запуск: python -m bot.userbot  (обычно отдельным docker-сервисом).
Сначала один раз получите сессию: python scripts/userbot_login.py
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from bot.agency_config import cfg
from bot.config import get_settings
from bot.database import crud
from bot.database.session import get_sessionmaker
from bot.keyboards.admin_kb import moderation_kb
from bot.logging_setup import init_sentry, setup_logging
from bot.net_session import RobustSession
from bot.services import analytics, publisher
from bot.services.ai_service import get_ai_service
from bot.services.autorepost import content_key, looks_like_listing, to_property_fields
from bot.services.duplicate_checker import check_duplicates
from bot.services.vision import get_vision_service
from bot.utils.formatters import format_property_card

setup_logging()
logger = logging.getLogger(__name__)

_SEEN_KEY = "autorepost:seen"


def _chats(raw: list[str]) -> list:
    """@name → строка, числовой id → int (Telethon принимает оба)."""
    out: list = []
    for c in raw:
        out.append(int(c) if c.lstrip("-").isdigit() else c)
    return out


class AutoReposter:
    def __init__(self, client, bot: Bot, redis) -> None:
        self._client = client
        self._bot = bot
        self._redis = redis
        self._settings = get_settings()
        self._sessionmaker = get_sessionmaker()
        self._dir = os.path.join(self._settings.media_dir, "autorepost")
        os.makedirs(self._dir, exist_ok=True)

    async def _vision_text(self, msg) -> str | None:
        """Прочитать объявление с картинки (для постов, где текст в фото)."""
        if not get_vision_service().is_enabled():
            return None
        tmp = os.path.join(self._dir, "_vision_tmp.jpg")
        try:
            await self._client.download_media(msg, file=tmp)
            with open(tmp, "rb") as fh:
                data = fh.read()
            return await get_vision_service().extract_listing_text(data)
        except Exception:  # noqa: BLE001
            return None
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    async def handle(self, text: str | None, photo_messages: list, source: str) -> None:
        text = (text or "").strip()
        logger.info("Авто-репост: сообщение из %s — %d фото, %d симв. текста",
                    source, len(photo_messages), len(text))

        # Если в подписи мало данных, но есть фото — читаем объявление с картинки.
        if photo_messages and not looks_like_listing(text):
            vtext = await self._vision_text(photo_messages[0])
            if vtext:
                text = (text + "\n" + vtext).strip()
                logger.info("Авто-репост: текст дочитан с фото (Vision), теперь %d симв.", len(text))

        if not looks_like_listing(text):
            logger.info("Авто-репост: не похоже на объявление — пропуск (%s)", source)
            return
        key = content_key(text)
        try:
            if await self._redis.sismember(_SEEN_KEY, key):
                logger.info("Авто-репост: уже обрабатывали этот текст (дубль) — пропуск")
                return
            await self._redis.sadd(_SEEN_KEY, key)  # помечаем сразу — без гонок/повторов
        except Exception:  # noqa: BLE001 - Redis недоступен не должен ронять обработку
            logger.exception("Авто-репост: ошибка Redis (дедуп), продолжаю без него")

        logger.info("Авто-репост: разбираю ИИ…")
        parsed = await get_ai_service().parse_listing(text or "")
        logger.info("Авто-репост: разобрано — тип=%s, цена=%s, район=%s",
                    parsed.get("deal_type"), parsed.get("price"), parsed.get("district"))
        fields = to_property_fields(parsed)
        if fields is None:
            logger.info("Авто-репост: пропуск (не распознан тип сделки), источник %s", source)
            return

        async with self._sessionmaker() as session:
            if await check_duplicates(session, fields):
                logger.info("Авто-репост: дубль уже есть в базе, пропуск")
                return

            paths: list[str] = []
            for i, msg in enumerate(photo_messages[: self._settings.autorepost_max_photos]):
                path = os.path.join(self._dir, f"{key}_{i}.jpg")
                try:
                    await self._client.download_media(msg, file=path)
                    paths.append(path)
                except Exception:  # noqa: BLE001
                    logger.warning("Не удалось скачать фото %s из %s", i, source)
            if paths:
                fields["photos"] = ",".join(paths)
            fields.setdefault("description", (text or "")[:800])

            prop = await crud.create_property(session, **fields)
            logger.info("Авто-репост: создан %s из %s (%d фото)", prop.id, source, len(paths))
            await analytics.track("listing_import", property_id=prop.id)

            if self._settings.autorepost_mode == "auto":
                await publisher.publish_property(self._bot, session, prop.id)
            else:
                await self._notify_moderation(prop)
            await session.commit()

    async def _notify_moderation(self, prop) -> None:
        text = "🤖 <b>Авто-импорт (на модерацию)</b>\n\n" + format_property_card(prop)
        kb = moderation_kb(prop.id)
        photos = prop.photo_list
        for admin_id in cfg().admin_ids:
            try:
                if photos and os.path.exists(photos[0]):
                    await self._bot.send_photo(admin_id, FSInputFile(photos[0]), caption=text, reply_markup=kb)
                elif photos:
                    await self._bot.send_photo(admin_id, photos[0], caption=text, reply_markup=kb)
                else:
                    await self._bot.send_message(admin_id, text, reply_markup=kb)
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось уведомить админа %s", admin_id)


async def main() -> None:
    settings = get_settings()
    # На ранних выходах спим 30с — чтобы docker restart не крутил тугой цикл,
    # пока .env донастраивают.
    if not settings.userbot_enabled:
        logger.info("Юзербот выключен (USERBOT_ENABLED не задан)")
        await asyncio.sleep(30)
        return
    if not (settings.telegram_api_id and settings.telegram_api_hash and settings.userbot_session):
        logger.error("Юзербот: нет TELEGRAM_API_ID / TELEGRAM_API_HASH / USERBOT_SESSION")
        await asyncio.sleep(30)
        return
    if not settings.source_channels:
        logger.error("Юзербот: не задан SOURCE_CHANNELS — нечего слушать")
        await asyncio.sleep(30)
        return

    import redis.asyncio as aioredis
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    client = TelegramClient(
        StringSession(settings.userbot_session), settings.telegram_api_id, settings.telegram_api_hash
    )
    bot = Bot(token=settings.bot_token, session=RobustSession(),
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    reposter = AutoReposter(client, bot, redis)
    await client.connect()
    if not await client.is_user_authorized():
        logger.error("Юзербот: сессия недействительна. Перегенерируйте USERBOT_SESSION "
                     "через scripts/userbot_login.py")
        await client.disconnect()
        return

    # Прогреваем кэш диалогов и резолвим источники в id — надёжнее фильтра chats=
    # у Telethon (юзернейм часто «молча» не совпадает при фильтрации апдейтов).
    from telethon import utils
    try:
        await client.get_dialogs()
    except Exception:  # noqa: BLE001
        pass
    allowed: set[int] = set()
    for ch in _chats(settings.source_channels):
        try:
            ent = await client.get_entity(ch)
            allowed.add(utils.get_peer_id(ent))
            logger.info("Источник подключён: %s (id=%s)", getattr(ent, "title", ch), utils.get_peer_id(ent))
        except Exception as exc:  # noqa: BLE001
            logger.error("Источник %s НЕ найден (аккаунт подписан? верный @username/id?): %s", ch, exc)

    async def _on_album(event) -> None:  # noqa: ANN001
        if event.chat_id not in allowed:
            return
        try:
            photos = [m for m in event.messages if m.photo]
            await reposter.handle(event.text, photos, _src(event))
        except Exception:  # noqa: BLE001
            logger.exception("Авто-репост: ошибка обработки альбома")

    async def _on_message(event) -> None:  # noqa: ANN001
        if event.chat_id not in allowed:
            return
        try:
            photos = [event.message] if event.message.photo else []
            await reposter.handle(event.raw_text, photos, _src(event))
        except Exception:  # noqa: BLE001
            logger.exception("Авто-репост: ошибка обработки сообщения")

    client.add_event_handler(_on_album, events.Album())
    client.add_event_handler(_on_message, events.NewMessage(func=lambda e: not e.grouped_id))

    if not allowed:
        logger.error("Юзербот: НИ ОДИН источник не подключён — проверьте SOURCE_CHANNELS и подписки аккаунта")
    logger.info("Юзербот запущен. Каналов подключено: %d (режим: %s)", len(allowed), settings.autorepost_mode)
    try:
        await client.run_until_disconnected()
    finally:
        await bot.session.close()


def _src(event) -> str:  # noqa: ANN001
    chat = getattr(event, "chat", None)
    return getattr(chat, "username", None) or str(getattr(event, "chat_id", "?"))


if __name__ == "__main__":
    init_sentry(service="userbot")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Юзербот остановлен по сигналу")
