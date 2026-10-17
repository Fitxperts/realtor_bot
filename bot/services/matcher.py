"""Подбор объектов недвижимости под заявку клиента и наоборот.

Цены объектов и бюджеты бывают в $ и в сумах — сравниваем в долларах по курсу
USD_RATE (bot/utils/money.py). Допуск по бюджету +10%.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import crud
from bot.database.models import (
    Client,
    ClientDealType,
    ClientStatus,
    Property,
    PropertyType,
)
from bot.utils.money import to_usd

# Сопоставление типа сделки клиента и типа объекта
_DEAL_MAP = {
    ClientDealType.rent: PropertyType.rent,
    ClientDealType.buy: PropertyType.sale,
}
# Обратное сопоставление: тип объекта → тип сделки клиента
_REVERSE_DEAL_MAP = {
    PropertyType.rent: ClientDealType.rent,
    PropertyType.sale: ClientDealType.buy,
}
_TOL = 0.10  # допуск по бюджету +10%


def _budget_usd(client: Client, rate: float) -> float | None:
    budget = to_usd(client.budget, client.currency, rate)
    return budget * (1 + _TOL) if budget is not None else None


async def find_for_client(session: AsyncSession, client: Client, *, limit: int = 5) -> list[Property]:
    """Найти активные объекты, подходящие под заявку клиента."""
    rate = get_settings().usd_rate
    return await crud.search_properties(
        session,
        prop_type=_DEAL_MAP.get(client.deal_type),
        district=client.district,
        rooms=client.rooms,
        max_price_usd=_budget_usd(client, rate),
        usd_rate=rate,
        limit=limit,
    )


async def clients_for_property(session: AsyncSession, prop: Property, *, limit: int = 100) -> list[Client]:
    """Клиенты с включённой подпиской (не закрытые), чьи заявки подходят под объект."""
    want_deal = _REVERSE_DEAL_MAP.get(prop.type)
    if want_deal is None:
        return []

    stmt = select(Client).where(
        Client.deal_type == want_deal,
        Client.status != ClientStatus.closed,
        Client.notify.is_(True),
    )
    candidates = list(await session.scalars(stmt))

    rate = get_settings().usd_rate
    price = to_usd(prop.price, prop.currency, rate)
    result: list[Client] = []
    for client in candidates:
        # Бюджет: цена объекта (в $) не выше бюджета клиента (в $) +10%
        budget = _budget_usd(client, rate)
        if price is not None and budget is not None and price > budget:
            continue
        # Район: если у клиента задан — объект должен ему соответствовать
        if client.district and prop.district and client.district.lower() not in prop.district.lower():
            continue
        # Комнаты: если заданы у клиента — должны совпадать
        if client.rooms is not None and prop.rooms is not None and client.rooms != prop.rooms:
            continue
        result.append(client)
        if len(result) >= limit:
            break
    return result
