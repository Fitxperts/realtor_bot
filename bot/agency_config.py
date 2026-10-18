"""Настройки текущего агентства: канал, админы, бренд, контакты, водяной знак.

- агентство №1 (то, что работало до SaaS) — по-прежнему из .env, ничего менять
  на сервере не нужно;
- остальные — из таблицы agencies (заполняются при подключении через бот платформы).
Реестр держится в памяти (load_agencies при старте и после изменений), поэтому
cfg() синхронный и дешёвый — его можно звать где угодно, хоть при оформлении поста.

Уведомления делятся на два уровня: агентству (заявки, модерация, отчёты) —
cfg().admin_ids; платформе (сбой ИИ, бэкапы, ошибки кода) — ADMIN_TELEGRAM_ID из .env.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import config as app_config
from bot.services import geo
from bot.tenancy import DEFAULT_AGENCY_ID, current_agency_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgencyConfig:
    id: int
    name: str
    channel_id: str = ""
    channel_url: str = ""
    publish_url: str = ""
    contacts: list[tuple[str, str]] = field(default_factory=list)
    watermark: str = ""
    admin_ids: list[int] = field(default_factory=list)
    language: str = "uz"
    bot_username: str = ""
    active: bool = True
    plan: str = "start"
    trial_until: dt.datetime | None = None
    paid_until: dt.datetime | None = None
    created_at: dt.datetime | None = None
    city: str = ""
    districts: tuple = ()          # ((имя, (варианты…)), …) — bot/services/geo.py

    def billing_state(self):
        """Состояние подписки сейчас (bot/services/billing.py)."""
        from bot.services.billing import compute_state

        return compute_state(self.id, self.trial_until, self.paid_until, self.created_at)[0]

    @property
    def readonly(self) -> bool:
        """Подписка не оплачена: приём и публикация объявлений остановлены."""
        from bot.services.billing import State

        return self.billing_state() == State.readonly

    def is_admin(self, telegram_id: int | None) -> bool:
        return telegram_id is not None and telegram_id in self.admin_ids


_registry: dict[int, AgencyConfig] = {}


def env_config() -> AgencyConfig:
    """Агентство №1 — настройки из .env (читаются при каждом вызове: правки видны сразу)."""
    settings = app_config.get_settings()
    return AgencyConfig(
        id=DEFAULT_AGENCY_ID,
        name=app_config.AGENCY_NAME,
        channel_id=settings.channel_id,
        channel_url=app_config.CHANNEL_URL,
        publish_url=app_config.BOT_PUBLISH_URL,
        contacts=list(app_config.AGENCY_CONTACTS),
        watermark=app_config.WATERMARK_TEXT,
        admin_ids=list(settings.admin_ids),
        city="Фергана",
        districts=geo.FERGANA,
    )


def from_row(row) -> AgencyConfig:
    """Строка agencies → настройки (для агентства №1 бренд и канал всё равно берутся из .env)."""
    username = row.bot_username or ""
    channel = row.channel_id or ""
    return AgencyConfig(
        id=row.id,
        name=row.name,
        channel_id=channel,
        channel_url=row.channel_url or (f"https://t.me/{channel[1:]}" if channel.startswith("@") else ""),
        publish_url=f"https://t.me/{username}?start=add" if username else "",
        contacts=app_config._parse_contacts(row.contacts or ""),
        watermark=row.watermark or "",
        admin_ids=app_config.Settings._parse_admin_ids(row.admin_ids or ""),
        language=row.language or "uz",
        bot_username=username,
        active=row.status != "disabled",
        plan=row.plan or "start",
        trial_until=row.trial_until,
        paid_until=row.paid_until,
        created_at=row.created_at,
        city=row.city or "",
        districts=geo.parse(row.districts) if row.districts else geo.for_city(row.city),
    )


def cfg(agency_id: int | None = None) -> AgencyConfig:
    """Настройки агентства (по умолчанию — текущего, из контекста апдейта/задачи)."""
    agency_id = agency_id or current_agency_id()
    known = _registry.get(agency_id)
    if agency_id == DEFAULT_AGENCY_ID:  # №1: .env всегда свежий, из БД — только бот и статус
        base = env_config()
        return replace(base, bot_username=known.bot_username, active=known.active) if known else base
    if known is not None:
        return known
    logger.warning("Агентство %s не загружено в реестр — пустые настройки", agency_id)
    return AgencyConfig(id=agency_id, name=f"Агентство {agency_id}", active=False)


def register(config: AgencyConfig) -> None:
    _registry[config.id] = config


def all_configs() -> list[AgencyConfig]:
    return sorted(_registry.values(), key=lambda c: c.id) or [env_config()]


async def load_agencies(session: AsyncSession) -> list[AgencyConfig]:
    """Перечитать все агентства из БД в реестр (при старте и после изменений)."""
    from bot.database.models import Agency

    rows = list(await session.scalars(select(Agency).order_by(Agency.id)))
    _registry.clear()
    for row in rows:
        register(from_row(row))
    logger.info("Агентств загружено: %d", len(rows))
    return all_configs()
