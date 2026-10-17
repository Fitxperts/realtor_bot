"""Точка входа РиелторБота: инициализация бота, диспетчера и запуск polling."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, ErrorEvent

from bot.config import get_settings
from bot.handlers import register_routers
from bot.logging_setup import init_sentry, setup_logging
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.i18n import LanguageMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.net_session import RobustSession
from bot.services.scheduler import setup_scheduler

_COMMANDS = [
    BotCommand(command="start", description="Начать / меню"),
    BotCommand(command="add", description="Разместить объект"),
    BotCommand(command="cancel", description="Отменить действие"),
    BotCommand(command="help", description="Помощь"),
]

setup_logging()
logger = logging.getLogger(__name__)


def _register_error_handler(dp: Dispatcher, bot: Bot, settings) -> None:
    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        # Сетевые таймауты/сбои связи — временные, aiogram переподключится сам.
        # Логируем как предупреждение, но админов не спамим.
        if isinstance(event.exception, TelegramNetworkError):
            logger.warning("Сетевой сбой Telegram (временный): %s", event.exception)
            return True
        logger.exception("Необработанная ошибка в хендлере", exc_info=event.exception)
        text = f"⚠️ Ошибка бота: {type(event.exception).__name__}: {event.exception}"[:1000]
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception:  # noqa: BLE001
                pass
        return True  # ошибка обработана


def _build(settings) -> tuple[Bot, Dispatcher, RedisStorage]:
    """Общая сборка бота, диспетчера и middleware (для polling и webhook)."""
    # Устойчивая к «плавающей» сети сессия: только IPv4 (обход мёртвого IPv6
    # до api.telegram.org) + короткие таймауты + автоповтор отправки.
    bot = Bot(
        token=settings.bot_token,
        session=RobustSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    dp.update.middleware(DbSessionMiddleware())
    dp.message.middleware(LanguageMiddleware())
    dp.callback_query.middleware(LanguageMiddleware())
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())

    register_routers(dp)
    _register_error_handler(dp, bot, settings)
    return bot, dp, storage


async def _run_polling(settings) -> None:
    bot, dp, storage = _build(settings)
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("РиелторБот запущен (polling)")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_my_commands(_COMMANDS)
        # polling_timeout поменьше → быстрее восстановление после сетевого блипа
        await dp.start_polling(bot, polling_timeout=20)
    finally:
        scheduler.shutdown(wait=False)
        await storage.close()
        await bot.session.close()
        logger.info("РиелторБот остановлен")


def _run_webhook(settings) -> None:
    """Режим webhook: Telegram сам присылает апдейты (нет пауз приёма).

    Бот слушает внутренний HTTP-порт; TLS и публичный домен обеспечивает
    реверс-прокси (Caddy) — см. docker-compose.webhook.yml.
    """
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    from aiohttp import web

    bot, dp, storage = _build(settings)
    scheduler = setup_scheduler(bot)
    webhook_url = settings.webhook_url.rstrip("/") + settings.webhook_path
    secret = settings.webhook_secret or None

    async def _set_webhook(drop: bool) -> None:
        await bot.set_webhook(
            webhook_url, secret_token=secret,
            drop_pending_updates=drop, allowed_updates=dp.resolve_used_update_types(),
        )

    async def _webhook_watchdog() -> None:
        """Каждые 30 мин проверяем вебхук и восстанавливаем, если слетел.

        Страхует от «тихой смерти»: если set_webhook когда-то не прошёл (NPM/серт
        были недоступны) — вебхук восстановится сам, без ручного вмешательства.
        """
        while True:
            await asyncio.sleep(1800)
            try:
                info = await bot.get_webhook_info()
                if (info.url or "") != webhook_url:
                    logger.warning("Webhook слетел (url=%r) — переустанавливаю", info.url)
                    await _set_webhook(drop=False)
                    logger.info("Webhook восстановлен watchdog'ом")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Watchdog webhook: %s", exc)

    async def on_startup(bot: Bot) -> None:
        scheduler.start()
        # set_webhook нефатален, НО с автоповтором: прокси/сертификат (NPM) при
        # старте могут быть ещё не готовы — раньше это приводило к «тихому»
        # зависанию без вебхука. Теперь ретраим с нарастающей паузой + watchdog.
        for attempt in range(1, 8):
            try:
                await _set_webhook(drop=True)
                await bot.set_my_commands(_COMMANDS)
                logger.info("РиелторБот запущен (webhook: %s)", webhook_url)
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("set_webhook: попытка %d/7 не удалась (%s), повтор через %dс",
                             attempt, exc, min(60, 10 * attempt))
                await asyncio.sleep(min(60, 10 * attempt))
        else:
            logger.error("Webhook не установлен после 7 попыток — watchdog продолжит попытки. "
                         "Проверьте домен/NPM/сертификат.")
        asyncio.create_task(_webhook_watchdog())

    async def on_shutdown(bot: Bot) -> None:
        scheduler.shutdown(wait=False)
        await storage.close()
        await bot.session.close()
        logger.info("РиелторБот остановлен")

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    async def health(_request) -> web.Response:
        """/health для Uptime Kuma: 200 — всё ок; 503 — вебхук слетел или БД недоступна."""
        from sqlalchemy import text

        from bot.database.session import get_sessionmaker

        status: dict = {"bot": "ok"}
        code = 200
        try:
            info = await bot.get_webhook_info()
            status["webhook_ok"] = (info.url or "") == webhook_url
            status["pending_updates"] = info.pending_update_count
            if info.last_error_message:
                status["telegram_last_error"] = info.last_error_message
            if not status["webhook_ok"]:
                code = 503
        except Exception as exc:  # noqa: BLE001
            status["webhook_ok"] = False
            status["telegram_error"] = type(exc).__name__
            code = 503
        try:
            async with get_sessionmaker()() as s:
                await s.execute(text("SELECT 1"))
            status["db"] = "ok"
        except Exception as exc:  # noqa: BLE001
            status["db"] = type(exc).__name__
            code = 503
        return web.json_response(status, status=code)

    app = web.Application()
    app.router.add_get("/health", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(
        app, path=settings.webhook_path
    )
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=settings.webhook_port)


if __name__ == "__main__":
    _settings = get_settings()
    init_sentry()
    # Схема БД применяется миграциями Alembic (alembic upgrade head) до старта.
    if _settings.webhook_url:
        _run_webhook(_settings)
    else:
        try:
            asyncio.run(_run_polling(_settings))
        except (KeyboardInterrupt, SystemExit):
            logger.info("Завершение работы по сигналу")
