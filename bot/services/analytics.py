"""Аналитика агентства: события, сводка за период, топ объектов, отчёт по понедельникам.

Что считаем (Bot API не отдаёт ботам просмотры постов канала — только то, что
проходит через бота):
- catalog_view   — карточка объекта показана в каталоге бота;
- lead_click     — нажали «Оставить заявку» (под постом канала или в каталоге);
- lead           — заявка по объекту с телефоном;
- request        — анкета «Подобрать жильё»;
- listing        — собственник разместил объект через бота;
- listing_import — объект пришёл авто-репостом из канала партнёра;
- published      — объект опубликован в канал;
- alert_sent     — объект прислан клиенту по подписке.
Запись события никогда не ломает основной сценарий (своя сессия, ошибки — в лог).
"""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot
from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.config import get_settings
from bot.database.models import BotUser, Event
from bot.database.session import get_sessionmaker
from bot.utils import timeutils

logger = logging.getLogger(__name__)

TYPES = ("catalog_view", "lead_click", "lead", "request", "listing", "listing_import", "published",
         "alert_sent")


async def track(type_: str, *, telegram_id: int | None = None, property_id: str | None = None) -> None:
    await track_many(type_, [property_id], telegram_id=telegram_id)


async def track_many(type_: str, property_ids: list[str | None], *, telegram_id: int | None = None) -> None:
    """Записать события (по одному на объект) отдельной сессией — сбой не трогает сценарий."""
    if not property_ids:
        return
    try:
        async with get_sessionmaker()() as session:
            session.add_all([Event(type=type_, telegram_id=telegram_id, property_id=pid) for pid in property_ids])
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Аналитика: событие %s не записано (%s)", type_, exc)


async def summary(session: AsyncSession, *, days: int, end: dt.datetime | None = None) -> dict:
    """Счётчики за [end - days, end): события по типам, новые пользователи, уникальные посетители."""
    end = end or timeutils.now()
    start = end - dt.timedelta(days=days)
    rows = await session.execute(
        select(Event.type, func.count()).where(Event.created_at >= start, Event.created_at < end)
        .group_by(Event.type)
    )
    result = dict.fromkeys(TYPES, 0) | dict(rows.all())
    result["new_users"] = int(await session.scalar(
        select(func.count()).select_from(BotUser).where(BotUser.created_at >= start, BotUser.created_at < end)
    ) or 0)
    result["visitors"] = int(await session.scalar(
        select(func.count(distinct(Event.telegram_id))).where(
            Event.created_at >= start, Event.created_at < end, Event.telegram_id.is_not(None),
            Event.type.in_(("catalog_view", "lead_click")))
    ) or 0)
    return result


async def top_properties(session: AsyncSession, *, days: int, limit: int = 5) -> list[tuple[str, int, int, int]]:
    """Топ объектов за период: (id, просмотры, клики «заявка», заявки), по заявкам и кликам."""
    start = timeutils.now() - dt.timedelta(days=days)

    def count_of(kind: str):
        return func.sum(case((Event.type == kind, 1), else_=0))

    stmt = (
        select(Event.property_id, count_of("catalog_view"), count_of("lead_click"), count_of("lead"))
        .where(Event.created_at >= start, Event.property_id.is_not(None),
               Event.type.in_(("catalog_view", "lead_click", "lead")))
        .group_by(Event.property_id)
    )
    rows = [(pid, int(v or 0), int(c or 0), int(lead or 0)) for pid, v, c, lead in await session.execute(stmt)]
    rows.sort(key=lambda r: (r[3], r[2], r[1]), reverse=True)
    return rows[:limit]


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "—"


def _delta(cur: int, prev: int) -> str:
    if cur == prev:
        return ""
    return f" ({'+' if cur > prev else '−'}{abs(cur - prev)})"


def format_report(cur: dict, prev: dict, top: list[tuple[str, int, int, int]], lang: str, *, title_key: str) -> str:
    lines = [i18n.t(title_key, lang), ""]
    for key, label in (("new_users", "an_new_users"), ("visitors", "an_visitors"),
                       ("catalog_view", "an_views"), ("lead_click", "an_clicks"), ("lead", "an_leads"),
                       ("request", "an_requests"), ("listing", "an_listings"), ("listing_import", "an_imports"),
                       ("published", "an_published"), ("alert_sent", "an_alerts")):
        lines.append(f"{i18n.t(label, lang)}: <b>{cur[key]}</b>{_delta(cur[key], prev[key])}")
    lines += ["", i18n.t("an_conv", lang, click=_pct(cur["lead"], cur["lead_click"]),
                         view=_pct(cur["lead_click"], cur["catalog_view"]))]
    if top:
        lines += ["", i18n.t("an_top", lang)]
        lines += [i18n.t("an_top_line", lang, id=pid, views=v, clicks=c, leads=lead) for pid, v, c, lead in top]
    lines += ["", i18n.t("an_note", lang)]
    return "\n".join(lines)


async def activity_report(session: AsyncSession, lang: str, *, days: int = 7,
                          title_key: str = "an_title_7d") -> str:
    """Сводка за последние `days` дней со сравнением с предыдущими `days` днями."""
    now = timeutils.now()
    cur = await summary(session, days=days, end=now)
    prev = await summary(session, days=days, end=now - dt.timedelta(days=days))
    top = await top_properties(session, days=30)
    return format_report(cur, prev, top, lang, title_key=title_key)


async def weekly_report(bot: Bot) -> None:
    """Задача планировщика (понедельник утром): отчёт за неделю каждому админу на его языке."""
    async with get_sessionmaker()() as session:
        for admin_id in get_settings().admin_ids:
            user = await session.get(BotUser, admin_id)
            lang = i18n.normalize(user.language if user else None)
            text = await activity_report(session, lang, title_key="an_title_week")
            try:
                await bot.send_message(admin_id, text)
            except Exception:  # noqa: BLE001
                logger.debug("Отчёт админу %s не доставлен", admin_id)
