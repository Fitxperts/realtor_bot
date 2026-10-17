"""Планировщик фоновых задач (APScheduler).

Шесть задач:
- автоподнятие активных объектов (ежедневно 10:00);
- напоминание о встрече за день (ежечасно);
- напоминание за 2 часа (каждые 15 минут);
- напоминание за 30 минут (каждые 5 минут);
- копия ночного бэкапа базы в Telegram + тревога, если бэкапа нет (04:10);
- сторож ИИ: тревога, если все ИИ-провайдеры лежат > 10 минут (каждые 5 минут);
- проверка актуальности объявлений (ежедневно 11:00).
В демо-режиме вместо автоподнятия и проверки актуальности — ночной сброс (05:00).
"""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import BUMP_INTERVAL_DAYS, TIMEZONE, get_settings
from bot.database import crud
from bot.database.models import Client
from bot.database.session import get_sessionmaker
from bot.services import publisher
from bot.services.ai_health import check_ai_outage
from bot.services.backups import ship_latest_backup
from bot.services.freshness import run_freshness
from bot.services.notify import notify_admins
from bot.utils import timeutils

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Задача 1. Автоподнятие
# ---------------------------------------------------------------------------
async def auto_bump(bot: Bot) -> None:
    threshold = timeutils.now() - dt.timedelta(days=BUMP_INTERVAL_DAYS)
    async with get_sessionmaker()() as session:
        due = await crud.properties_due_for_bump(session, threshold)
        bumped = 0
        for prop in due:
            try:
                await publisher.bump_property(bot, session, prop.id)
                bumped += 1
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось поднять объект %s", prop.id)
    if bumped:
        await notify_admins(bot, f"📢 Автоподнятие: поднято {bumped} объявлений.")
    logger.info("Автоподнятие: обработано %d", bumped)


# ---------------------------------------------------------------------------
# Задачи 2-4. Напоминания о встречах
# ---------------------------------------------------------------------------
async def remind(
    bot: Bot, lower: dt.timedelta, upper: dt.timedelta, flag: str, human: str
) -> None:
    now = timeutils.now()
    after, before = now + lower, now + upper
    async with get_sessionmaker()() as session:
        meetings = await crud.meetings_for_reminder(session, after=after, before=before, flag=flag)
        for meeting in meetings:
            client = await session.get(Client, meeting.client_id)
            prop = await crud.get_property(session, meeting.property_id)
            when = meeting.datetime.strftime("%d.%m %H:%M")
            address = (prop.address if prop else None) or meeting.property_id

            # Клиенту
            if client:
                try:
                    await bot.send_message(
                        client.telegram_id,
                        f"⏰ Напоминание: {human} у вас показ объекта "
                        f"{meeting.property_id} ({address}) в {when}.",
                    )
                except Exception:  # noqa: BLE001 - клиент мог заблокировать бота
                    logger.debug("Не удалось напомнить клиенту %s", meeting.client_id)

            # Админам
            info = f"{client.name or '—'} ({client.phone or '—'})" if client else "—"
            await notify_admins(
                bot,
                f"⏰ Напоминание ({human}): встреча {meeting.id}\n"
                f"Клиент: {info}\nОбъект: {meeting.property_id} · {address}\nКогда: {when}",
            )

            setattr(meeting, flag, True)
        await session.commit()
    if meetings:
        logger.info("Напоминания '%s': отправлено %d", flag, len(meetings))


# ---------------------------------------------------------------------------
# Настройка
# ---------------------------------------------------------------------------
def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    if get_settings().demo_mode:
        from bot.services.demo import reset_demo

        # примеры не поднимаем (канал демо не засоряется), а каждую ночь всё чистим
        scheduler.add_job(
            reset_demo, trigger="cron", hour=5, minute=0, args=[bot],
            id="demo_reset", replace_existing=True,
        )
    else:
        scheduler.add_job(
            auto_bump, trigger="cron", hour=10, minute=0, args=[bot],
            id="auto_bump", replace_existing=True,
        )
        scheduler.add_job(
            run_freshness, trigger="cron", hour=11, minute=0, args=[bot],
            id="freshness", replace_existing=True,
        )
    # Непересекающиеся окна: за день (2ч..24ч), за 2 часа (30м..2ч), за 30 минут (0..30м)
    scheduler.add_job(
        remind, trigger="interval", hours=1,
        args=[bot, dt.timedelta(hours=2), dt.timedelta(days=1), "reminded_1d", "завтра"],
        id="remind_1d", replace_existing=True,
    )
    scheduler.add_job(
        remind, trigger="interval", minutes=15,
        args=[bot, dt.timedelta(minutes=30), dt.timedelta(hours=2), "reminded_2h", "через ~2 часа"],
        id="remind_2h", replace_existing=True,
    )
    scheduler.add_job(
        remind, trigger="interval", minutes=5,
        args=[bot, dt.timedelta(0), dt.timedelta(minutes=30), "reminded_30m", "через 30 минут"],
        id="remind_30m", replace_existing=True,
    )
    # Сервис backup делает дамп после 03:00 — к 04:10 он точно готов
    scheduler.add_job(
        ship_latest_backup, trigger="cron", hour=4, minute=10, args=[bot],
        id="ship_backup", replace_existing=True,
    )
    scheduler.add_job(
        check_ai_outage, trigger="interval", minutes=5, args=[bot],
        id="ai_watchdog", replace_existing=True,
    )

    logger.info("Планировщик настроен: %d задач (TZ=%s)", len(scheduler.get_jobs()), TIMEZONE)
    return scheduler
