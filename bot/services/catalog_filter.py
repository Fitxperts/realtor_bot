"""Фильтры каталога: сделка, район, комнаты, цена «до».

Фильтр живёт прямо в данных кнопки (``rent:2:3:1``) — без состояния FSM, поэтому
не мешает анкетам и переживает перезапуск бота. Фильтруем в Python по активным
объявлениям агентства (их сотни, не миллионы): так одинаково работают Postgres
и SQLite, а районы сравниваются с учётом написаний (Qirg'uli = Киргули).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.agency_config import cfg
from bot.config import get_settings
from bot.database.models import Property, PropertyStatus, PropertyType
from bot.services.offline_parser import known_district
from bot.utils.money import to_usd


def districts() -> list[str]:
    """Районы города текущего агентства — в порядке справочника (индекс идёт в данные кнопки)."""
    return [name for name, _ in cfg().districts]


ROOMS = (1, 2, 3, 4)                       # 4 = «4 и больше»
PRICE_BUCKETS = {                          # потолок цены в долларах
    "rent": (200, 300, 400, 500, 700),
    "sale": (20_000, 30_000, 40_000, 50_000, 70_000, 100_000),
}
_TYPE = {"rent": PropertyType.rent, "sale": PropertyType.sale}


@dataclass(frozen=True)
class CatalogFilter:
    deal: str = "all"              # rent | sale | all
    district: int | None = None    # индекс в districts()
    rooms: int | None = None       # 1..4 (4 = 4+)
    price: int | None = None       # индекс в PRICE_BUCKETS[deal]

    def encode(self) -> str:
        def part(value):
            return "x" if value is None else str(value)
        return f"{self.deal}:{part(self.district)}:{part(self.rooms)}:{part(self.price)}"

    @classmethod
    def decode(cls, raw: str) -> CatalogFilter:
        """Разбор из данных кнопки; мусор → безопасные значения (кнопки старых версий)."""
        parts = (raw.split(":") + ["x"] * 4)[:4]

        def num(value, limit):
            return int(value) if value.isdigit() and int(value) < limit else None

        deal = parts[0] if parts[0] in ("rent", "sale", "all") else "all"
        rooms = num(parts[2], 5)
        f = cls(deal, num(parts[1], len(districts())), rooms if rooms else None,
                num(parts[3], len(PRICE_BUCKETS.get(deal, ()))))
        return f

    def with_(self, **changes) -> CatalogFilter:
        f = replace(self, **changes)
        if f.deal not in PRICE_BUCKETS:  # у «все» нет общей шкалы цен
            f = replace(f, price=None)
        return f

    @property
    def max_usd(self) -> float | None:
        if self.price is None or self.deal not in PRICE_BUCKETS:
            return None
        return float(PRICE_BUCKETS[self.deal][self.price])

    @property
    def is_empty(self) -> bool:
        return self.district is None and self.rooms is None and self.price is None

    def matches(self, prop: Property, rate: float) -> bool:
        if self.district is not None and known_district(prop.district) != districts()[self.district]:
            return False
        if self.rooms is not None:
            if prop.rooms is None or (prop.rooms != self.rooms if self.rooms < 4 else prop.rooms < 4):
                return False
        if self.max_usd is not None:
            price = to_usd(prop.price, prop.currency, rate)
            if price is None or price > self.max_usd:
                return False
        return True


async def _active(session: AsyncSession, deal: str) -> list[Property]:
    stmt = select(Property).where(Property.status == PropertyStatus.active)
    if deal in _TYPE:
        stmt = stmt.where(Property.type == _TYPE[deal])
    stmt = stmt.order_by(Property.last_bump.desc().nullslast(), Property.created_at.desc())
    return list(await session.scalars(stmt))


async def filtered(session: AsyncSession, f: CatalogFilter) -> list[Property]:
    """Все подходящие активные объявления — в порядке каталога (свежие/поднятые первыми)."""
    rate = get_settings().usd_rate
    return [p for p in await _active(session, f.deal) if f.matches(p, rate)]


async def district_counts(session: AsyncSession, f: CatalogFilter) -> list[tuple[int, str, int]]:
    """Районы, где есть объявления при остальных условиях фильтра: (индекс, имя, сколько)."""
    rate = get_settings().usd_rate
    base = f.with_(district=None)
    counts: dict[str, int] = {}
    for prop in await _active(session, f.deal):
        name = known_district(prop.district)
        if name and base.matches(prop, rate):
            counts[name] = counts.get(name, 0) + 1
    return [(i, name, counts[name]) for i, name in enumerate(districts()) if counts.get(name)]
