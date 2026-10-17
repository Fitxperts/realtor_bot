"""Изоляция агентств (white-label): каждое видит и меняет только свои данные."""
from __future__ import annotations

from sqlalchemy import delete, func, select

from bot.database import crud
from bot.database.models import (
    Agency,
    Client,
    ClientDealType,
    Event,
    Property,
    PropertyKind,
    PropertyStatus,
    PropertyType,
)
from bot.services import alerts, analytics, matcher
from bot.services.catalog_filter import CatalogFilter, filtered
from bot.tenancy import ALL_AGENCIES, agency_scope, current_agency_id
from tests.conftest import FakeBot


async def _prop(session, **kw):
    kw.setdefault("type", PropertyType.rent)
    return await crud.create_property(session, property_kind=PropertyKind.apartment, status=PropertyStatus.active,
                                      district="Киргули", price=300, currency="$", **kw)


async def _two_agencies(session):
    session.add_all([Agency(id=1, name="Первое"), Agency(id=2, name="Второе")])
    await session.commit()
    a1 = await _prop(session)                       # без явного агентства — №1 (как до SaaS)
    with agency_scope(2):
        a2 = await _prop(session)
        await crud.create_client(session, telegram_id=500, deal_type=ClientDealType.rent, budget=400,
                                 currency="$", district="Киргули")
    return a1, a2


async def test_insert_gets_current_agency_and_ids_stay_unique(session):
    a1, a2 = await _two_agencies(session)
    assert (a1.agency_id, a2.agency_id) == (1, 2)
    assert a1.id != a2.id                           # первичный ключ общий на платформу — без коллизий
    assert current_agency_id() == 1


async def test_reads_are_isolated(session):
    a1, a2 = await _two_agencies(session)
    session.expunge_all()                           # без кэша сессии — честный запрос в БД
    assert [p.id for p in await filtered(session, CatalogFilter())] == [a1.id]
    assert await crud.get_property(session, a2.id) is None          # чужой объект по ID не открыть
    assert await crud.list_clients(session) == []
    with agency_scope(2):
        session.expunge_all()
        assert [p.id for p in await filtered(session, CatalogFilter())] == [a2.id]
        assert len(await crud.list_clients(session)) == 1
        assert await crud.get_property(session, a1.id) is None


async def test_bulk_update_and_delete_stay_inside_agency(session):
    a1, a2 = await _two_agencies(session)
    with agency_scope(2):
        await session.execute(delete(Client))
        await session.execute(delete(Property))
        await session.commit()
    session.expunge_all()
    assert await crud.get_property(session, a1.id) is not None     # агентство №1 не тронуто
    everything = await session.scalar(select(func.count()).select_from(Property).execution_options(**ALL_AGENCIES))
    assert everything == 1


async def test_same_person_has_separate_profiles(session):
    session.add_all([Agency(id=1, name="Первое"), Agency(id=2, name="Второе")])
    await session.commit()
    await crud.get_or_create_user(session, 777)
    await crud.set_language(session, 777, "ru")
    with agency_scope(2):
        user = await crud.get_or_create_user(session, 777)
        assert user.language is None                # в новом агентстве — новый профиль
        await crud.set_language(session, 777, "uz")
    assert (await crud.get_user(session, 777)).language == "ru"


async def test_matching_and_alerts_do_not_cross_agencies(session):
    a1, _ = await _two_agencies(session)            # клиент 500 — в агентстве №2
    assert await matcher.clients_for_property(session, a1) == []
    assert await alerts.send_property_alerts(FakeBot(), session, a1) == 0


async def test_analytics_scoped(session):
    session.add_all([Event(type="lead"), Event(type="lead")])
    await session.commit()
    with agency_scope(2):
        session.add(Event(type="lead"))
        await session.commit()
        assert (await analytics.summary(session, days=1))["lead"] == 1
    assert (await analytics.summary(session, days=1))["lead"] == 2


async def test_platform_query_sees_all(session):
    await _two_agencies(session)
    rows = await session.scalars(select(Property.agency_id).execution_options(**ALL_AGENCIES))
    assert sorted(rows) == [1, 2]
