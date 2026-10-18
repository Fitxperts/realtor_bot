"""Лимит ИИ-разборов по тарифу: «Старт» 150, «Про» 600, «Сеть» 2000 объявлений в месяц.

Единица — одно объявление, разобранное ИИ (текст, голос или скрин — неважно).
Счётчик — события «ai_parse» в аналитике за текущий календарный месяц.
Лимит исчерпан → объявления разбираются офлайн (bot/services/offline_parser.py),
голос и скрины — просим прислать текстом; админам агентства — одно сообщение
в месяц. Агентство №1 (своё, из .env) — без лимита.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import func, select

from bot.agency_config import cfg
from bot.database.models import Event
from bot.database.session import get_sessionmaker
from bot.services import analytics, billing
from bot.tenancy import DEFAULT_AGENCY_ID, current_agency_id
from bot.utils import timeutils

logger = logging.getLogger(__name__)

SPENT, NOTICE = "ai_parse", "ai_quota_notice"


def month_start(now: dt.datetime | None = None) -> dt.datetime:
    now = now or timeutils.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def limit() -> int | None:
    """Лимит текущего агентства на месяц (None — без лимита)."""
    if current_agency_id() == DEFAULT_AGENCY_ID:
        return None
    return billing.PLANS.get(cfg().plan, billing.PLANS["start"])["ai_month"]


async def _count(type_: str) -> int:
    async with get_sessionmaker()() as session:
        return await session.scalar(select(func.count()).select_from(Event).where(
            Event.type == type_, Event.created_at >= month_start())) or 0


async def used() -> int:
    return await _count(SPENT)


async def allow() -> bool:
    """Можно ли ещё раз воспользоваться ИИ. Нет — заодно (раз в месяц) сообщить админам."""
    cap = limit()
    if cap is None:
        return True
    try:
        if await used() < cap:
            return True
    except Exception:  # noqa: BLE001 - сбой счётчика не должен останавливать приём объявлений
        logger.exception("Лимит ИИ: не удалось посчитать — пропускаю")
        return True
    await _notify_once(cap)
    return False


async def spend() -> None:
    if limit() is not None:
        await analytics.track(SPENT)


async def _notify_once(cap: int) -> None:
    from bot import bots
    from bot.services.notify import notify_admins

    if await _count(NOTICE):
        return
    await analytics.track(NOTICE)
    agency_bot = bots.bot_for(current_agency_id())
    if agency_bot is None:
        return
    plan = billing.PLANS.get(cfg().plan, {}).get("name", cfg().plan)
    await notify_admins(agency_bot,
                        f"🤖 Лимит ИИ-разборов на этот месяц исчерпан ({cap} объявлений, тариф «{plan}»). "
                        "Объявления по-прежнему принимаются: текст разбирается без ИИ (проверяйте поля "
                        "внимательнее), голос и скрины — просим прислать текстом. Лимит обновится 1-го числа; "
                        "больше разборов — на тарифе выше." + billing.pay_hint())
    logger.info("Агентство %s: лимит ИИ исчерпан (%s)", current_agency_id(), cap)
