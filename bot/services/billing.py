"""Подписка агентства: пробный период → оплачено → льгота → «только чтение».

- новое агентство: 14 дней бесплатно (trial_until);
- оплата продлевает paid_until (bot/services/payments.py — Payme/Click);
- срок кончился → 3 дня льготы (всё работает, напоминаем);
- дальше «только чтение»: каталог и заявки клиентов работают (агентство не теряет
  клиентов), но приём и публикация объявлений остановлены до оплаты;
- агентство №1 (своё, из .env) — без подписки.
"""
from __future__ import annotations

import datetime as dt
import enum
import logging
import os

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.tenancy import DEFAULT_AGENCY_ID
from bot.utils import timeutils

logger = logging.getLogger(__name__)

TRIAL_DAYS = 14
GRACE_DAYS = 3


def _price(name: str, default: int) -> int:
    return int(os.getenv(name, "") or default)


# Цены в сумах (Payme и Click принимают сумы). Переопределяются в .env.
PLANS: dict[str, dict] = {
    "start": {"name": "Старт", "uzs": _price("PRICE_START_UZS", 420_000), "ai_month": 150},
    "pro": {"name": "Про", "uzs": _price("PRICE_PRO_UZS", 590_000), "ai_month": 600},
    "network": {"name": "Сеть", "uzs": _price("PRICE_NETWORK_UZS", 1_200_000), "ai_month": 2_000},
}


class State(str, enum.Enum):
    trial = "trial"
    active = "active"
    grace = "grace"
    readonly = "readonly"


def compute_state(agency_id: int, trial_until: dt.datetime | None, paid_until: dt.datetime | None,
                  created_at: dt.datetime | None = None, now: dt.datetime | None = None
                  ) -> tuple[State, dt.datetime | None]:
    """(состояние, до какого момента оно длится)."""
    if agency_id == DEFAULT_AGENCY_ID:
        return State.active, None
    now = now or timeutils.now()
    if trial_until is None and created_at is not None:   # агентства до появления подписок
        trial_until = created_at + dt.timedelta(days=TRIAL_DAYS)
    if paid_until and now < paid_until:
        return State.active, paid_until
    if trial_until and now < trial_until and not paid_until:
        return State.trial, trial_until
    end = max(d for d in (paid_until, trial_until) if d) if (paid_until or trial_until) else None
    if end and now < end + dt.timedelta(days=GRACE_DAYS):
        return State.grace, end + dt.timedelta(days=GRACE_DAYS)
    return State.readonly, None


def extend(paid_until: dt.datetime | None, months: int, now: dt.datetime | None = None) -> dt.datetime:
    """Продлить подписку: от текущего срока, если он ещё не кончился, иначе от сегодня."""
    now = now or timeutils.now()
    start = paid_until if paid_until and paid_until > now else now
    return start + dt.timedelta(days=30 * months)


def notice_key(state: State, until: dt.datetime | None, now: dt.datetime | None = None) -> str | None:
    """Какое напоминание положено сейчас (None — никакого)."""
    now = now or timeutils.now()
    days = (until - now).days if until else None
    # за 3 дня — «скоро», в последний день — ещё раз
    if state in (State.trial, State.active) and days is not None and days <= 3:
        prefix = "trial" if state == State.trial else "paid"
        return f"{prefix}_soon" if days >= 1 else f"{prefix}_last"
    if state == State.grace:
        return "grace"
    if state == State.readonly:
        return "readonly"
    return None


_TEXTS = {
    "trial": "🕑 Пробный период заканчивается {when}. Чтобы бот работал дальше без перерыва — оплатите подписку.",
    "paid": "🕑 Подписка заканчивается {when}. Продлите, чтобы бот работал без перерыва.",
    "grace": "⚠️ Подписка закончилась. Бот работает ещё до {when} — потом приём и публикация объявлений "
             "остановятся (каталог и заявки клиентов продолжат работать).",
    "readonly": "⛔ Подписка не оплачена: приём и публикация объявлений остановлены. Каталог и заявки клиентов "
                "работают, данные сохранены. Оплатите — и всё включится сразу.",
}


def pay_hint() -> str:
    username = os.getenv("PLATFORM_BOT_USERNAME", "").strip().lstrip("@")
    where = f"@{username}" if username else "бот платформы"
    return f"\n\nОплата: {where} → /pay (Payme или Click)."


async def send_reminder(bot: Bot, session: AsyncSession) -> str | None:
    """Задача планировщика (для каждого агентства): одно напоминание на каждый этап, без повторов."""
    from bot.agency_config import from_row, register
    from bot.database.models import Agency
    from bot.services.notify import notify_admins
    from bot.tenancy import current_agency_id

    row = await session.get(Agency, current_agency_id())
    if row is None or row.id == DEFAULT_AGENCY_ID or row.status == "disabled":
        return None
    state, until = compute_state(row.id, row.trial_until, row.paid_until, row.created_at)
    key = notice_key(state, until)
    if key is None or key == row.billing_notice:
        return None
    when = f"{until:%d.%m.%Y}" if until else ""
    await notify_admins(bot, _TEXTS[key.split("_")[0]].format(when=when) + pay_hint())
    row.billing_notice = key
    await session.commit()
    register(from_row(row))
    logger.info("Агентство %s: напоминание об оплате %s", row.id, key)
    return key


async def reminders_job(bot: Bot) -> None:
    from bot.database.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        await send_reminder(bot, session)
