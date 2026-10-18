"""Точка входа РиелторБота: диспетчер, боты всех агентств, webhook или polling.

Один процесс обслуживает основной бот (агентство №1, BOT_TOKEN, путь /webhook) и
ботов подключённых агентств (путь /webhook/a<id>, свой секрет у каждого).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent

from bot import bots
from bot.agency_config import cfg, load_agencies
from bot.bots import AgencyMiddleware
from bot.config import get_settings
from bot.handlers import register_routers
from bot.logging_setup import init_sentry, setup_logging
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.i18n import LanguageMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.services.scheduler import setup_scheduler
from bot.tenancy import DEFAULT_AGENCY_ID, current_agency_id
from bot.web.agency_webhook import AgencyRequestHandler, agency_path
from bot.web.landing import register_landing

_COMMANDS = [
    BotCommand(command="start", description="Начать / меню"),
    BotCommand(command="add", description="Разместить объект"),
    BotCommand(command="cancel", description="Отменить действие"),
    BotCommand(command="help", description="Помощь"),
]
# Владельцу платформы в меню — служебные команды (агентствам они недоступны)
_PLATFORM_COMMANDS = _COMMANDS + [
    BotCommand(command="ai_status", description="Проверка ИИ-провайдеров"),
    BotCommand(command="ai_models", description="Модели провайдера: /ai_models groq"),
    BotCommand(command="backup", description="Свежий бэкап базы файлом"),
    BotCommand(command="ai_eval", description="Проверка качества разбора (50 объявлений)"),
]

setup_logging()
logger = logging.getLogger(__name__)


async def _set_commands(bot: Bot, agency_id: int = DEFAULT_AGENCY_ID) -> None:
    await bot.set_my_commands(_COMMANDS)
    platform = set(get_settings().admin_ids)
    for admin_id in cfg(agency_id).admin_ids:  # служебное меню — только владельцу платформы
        if admin_id not in platform:
            continue
        try:
            await bot.set_my_commands(_PLATFORM_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as exc:  # noqa: BLE001 - админ ещё не писал боту
            logger.warning("Меню админа %s не установлено: %s", admin_id, exc)


async def _load_platform() -> None:
    """Агентства и их боты из БД. Сбой (БД ещё не готова) — работаем с основным ботом."""
    from bot.database.session import get_sessionmaker

    try:
        async with get_sessionmaker()() as session:
            await load_agencies(session)
            loaded = await bots.load_agency_bots(session)
        logger.info("Боты агентств: %s", loaded or "только основной")
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось загрузить агентства — работает только основной бот")


_background: set[asyncio.Task] = set()


def _start_demo(bot: Bot, settings) -> None:
    """Демо-режим: примеры в базе и в канале — в фоне, чтобы не задерживать старт."""
    if not settings.demo_mode:
        return
    from bot.services.demo import bootstrap

    task = asyncio.create_task(bootstrap(bot))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _register_error_handler(dp: Dispatcher, bot: Bot, settings) -> None:
    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        # Сетевые таймауты/сбои связи — временные, aiogram переподключится сам.
        # Логируем как предупреждение, но админов не спамим.
        if isinstance(event.exception, TelegramNetworkError):
            logger.warning("Сетевой сбой Telegram (временный): %s", event.exception)
            return True
        logger.exception("Необработанная ошибка в хендлере", exc_info=event.exception)
        text = (f"⚠️ Ошибка бота (агентство {current_agency_id()}): "
                f"{type(event.exception).__name__}: {event.exception}")[:1000]
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
    bot = bots.make_bot(settings.bot_token)
    bots.register_bot(DEFAULT_AGENCY_ID, bot, settings.webhook_secret or None)
    # bot_id в ключе состояния: человек в ботах двух агентств — две независимые анкеты
    storage = RedisStorage.from_url(settings.redis_url, key_builder=DefaultKeyBuilder(with_bot_id=True))
    dp = Dispatcher(storage=storage)

    dp.update.outer_middleware(AgencyMiddleware())  # агентство — по боту, до любых запросов к БД
    dp.update.middleware(DbSessionMiddleware())
    dp.message.middleware(LanguageMiddleware())
    dp.callback_query.middleware(LanguageMiddleware())
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())

    register_routers(dp)
    _register_error_handler(dp, bot, settings)
    return bot, dp, storage


def _platform(settings, storage):
    """Бот платформы, если задан PLATFORM_BOT_TOKEN (иначе None)."""
    if not settings.platform_bot_token:
        return None, None
    from bot.platform.runtime import build_platform

    return build_platform(settings.platform_bot_token, storage)


async def _run_polling(settings) -> None:
    bot, dp, storage = _build(settings)
    await _load_platform()
    platform_bot, platform_dp = _platform(settings, storage)
    scheduler = setup_scheduler(bot)
    scheduler.start()
    all_bots = bots.agency_bots()
    logger.info("РиелторБот запущен (polling, ботов: %d)", len(all_bots))
    try:
        for agency_id, agency_bot in all_bots.items():
            await agency_bot.delete_webhook(drop_pending_updates=True)
            await _set_commands(agency_bot, agency_id)
        _start_demo(bot, settings)
        # polling_timeout поменьше → быстрее восстановление после сетевого блипа.
        # Новые агентства в режиме polling подхватываются после перезапуска.
        runners = [dp.start_polling(*all_bots.values(), polling_timeout=20)]
        if platform_bot is not None:
            await platform_bot.delete_webhook(drop_pending_updates=True)
            runners.append(platform_dp.start_polling(platform_bot, polling_timeout=20, handle_signals=False))
        await asyncio.gather(*runners)
    finally:
        scheduler.shutdown(wait=False)
        await storage.close()
        for agency_bot in all_bots.values():
            await agency_bot.session.close()
        if platform_bot is not None:
            await platform_bot.session.close()
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
    platform_bot, platform_dp = _platform(settings, storage)
    platform_path = settings.webhook_path.rstrip("/") + "/platform"

    def url_for(agency_id: int) -> str:
        return settings.webhook_url.rstrip("/") + agency_path(settings.webhook_path, agency_id)

    async def _set_webhook(agency_id: int, agency_bot: Bot, drop: bool) -> None:
        secret = settings.webhook_secret or None if agency_id == DEFAULT_AGENCY_ID else bots.secret_for(agency_id)
        await agency_bot.set_webhook(
            url_for(agency_id), secret_token=secret,
            drop_pending_updates=drop, allowed_updates=dp.resolve_used_update_types(),
        )

    async def _install(agency_id: int, agency_bot: Bot) -> None:
        """Вебхук + меню бота агентства (при старте и при подключении нового агентства)."""
        await _set_webhook(agency_id, agency_bot, drop=True)
        await _set_commands(agency_bot, agency_id)
        logger.info("Бот агентства %s подключён: %s", agency_id, url_for(agency_id))

    async def _webhook_watchdog() -> None:
        """Каждые 30 мин проверяем вебхуки ВСЕХ ботов и восстанавливаем слетевшие.

        Страхует от «тихой смерти»: если set_webhook когда-то не прошёл (NPM/серт
        были недоступны) — вебхук восстановится сам, без ручного вмешательства.
        """
        while True:
            await asyncio.sleep(1800)
            for agency_id, agency_bot in bots.agency_bots().items():
                try:
                    info = await agency_bot.get_webhook_info()
                    if (info.url or "") != url_for(agency_id):
                        logger.warning("Webhook агентства %s слетел (url=%r) — переустанавливаю",
                                       agency_id, info.url)
                        await _set_webhook(agency_id, agency_bot, drop=False)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Watchdog webhook (агентство %s): %s", agency_id, exc)

    async def on_startup(bot: Bot) -> None:
        await _load_platform()
        bots.set_activation_hook(_install)
        scheduler.start()
        _start_demo(bot, settings)
        # set_webhook нефатален, НО с автоповтором: прокси/сертификат (NPM) при
        # старте могут быть ещё не готовы — раньше это приводило к «тихому»
        # зависанию без вебхука. Теперь ретраим с нарастающей паузой + watchdog.
        pending = bots.agency_bots()
        for attempt in range(1, 8):
            for agency_id, agency_bot in list(pending.items()):
                try:
                    await _install(agency_id, agency_bot)
                    pending.pop(agency_id)
                except Exception as exc:  # noqa: BLE001
                    logger.error("set_webhook (агентство %s): попытка %d/7 не удалась (%s)",
                                 agency_id, attempt, exc)
            if not pending:
                logger.info("РиелторБот запущен (webhook: %s, ботов: %d)", webhook_url,
                            len(bots.agency_bots()))
                break
            await asyncio.sleep(min(60, 10 * attempt))
        else:
            logger.error("Webhook не установлен после 7 попыток (агентства %s) — watchdog продолжит "
                         "попытки. Проверьте домен/NPM/сертификат.", list(pending))
        task = asyncio.create_task(_webhook_watchdog())
        _background.add(task)
        if platform_bot is not None:
            try:
                await platform_bot.set_webhook(
                    settings.webhook_url.rstrip("/") + platform_path, secret_token=settings.webhook_secret or None,
                    drop_pending_updates=True, allowed_updates=platform_dp.resolve_used_update_types())
                logger.info("Бот платформы подключён: %s", platform_path)
            except Exception as exc:  # noqa: BLE001
                logger.error("Бот платформы: webhook не установлен (%s)", exc)

    async def on_shutdown(bot: Bot) -> None:
        bots.set_activation_hook(None)
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
        broken = []
        for agency_id, agency_bot in bots.agency_bots().items():
            if agency_id == DEFAULT_AGENCY_ID:
                continue
            try:
                if ((await agency_bot.get_webhook_info()).url or "") != url_for(agency_id):
                    broken.append(agency_id)
            except Exception:  # noqa: BLE001
                broken.append(agency_id)
        status["agencies"] = len(bots.agency_bots())
        if broken:
            status["agencies_webhook_broken"] = broken
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
    register_landing(app)  # https://<домен>/ — страница продукта для агентств
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.webhook_secret or None).register(
        app, path=settings.webhook_path
    )
    AgencyRequestHandler(dispatcher=dp).register_routes(app, settings.webhook_path)  # /webhook/a<id>
    if platform_bot is not None:  # /webhook/platform — онбординг агентств и супер-админка
        SimpleRequestHandler(dispatcher=platform_dp, bot=platform_bot,
                             secret_token=settings.webhook_secret or None).register(app, path=platform_path)
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
