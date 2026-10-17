"""Тесты слоя доступа к данным."""
import datetime as dt

from bot.database import crud
from bot.database.models import (
    ClientDealType,
    ClientStatus,
    MeetingStatus,
    PropertyKind,
    PropertyStatus,
    PropertyType,
    UserRole,
)
from bot.utils import timeutils


async def test_user_role_and_choose(session):
    u = await crud.get_or_create_user(session, 10, full_name="Новый")
    assert u.role == UserRole.client and u.role_chosen is False
    await crud.choose_role(session, 10, UserRole.owner)
    u2 = await crud.get_or_create_user(session, 10)
    assert u2.role == UserRole.owner and u2.role_chosen is True


async def test_admin_user(session):
    a = await crud.get_or_create_user(session, 99, is_admin=True)
    assert a.role == UserRole.admin and a.role_chosen is True


async def test_id_generation(session):
    c = await crud.create_client(session, telegram_id=1, deal_type=ClientDealType.rent)
    assert c.id == "CLT_001"
    p = await crud.create_property(session, property_kind=PropertyKind.apartment,
                                   type=PropertyType.rent, status=PropertyStatus.pending)
    assert p.id == "APT_1001"
    h = await crud.create_property(session, property_kind=PropertyKind.house,
                                   type=PropertyType.sale, status=PropertyStatus.pending)
    assert h.id == "HSE_1001"


async def test_search_and_pagination(session):
    for _ in range(7):
        await crud.create_property(session, property_kind=PropertyKind.apartment,
                                   type=PropertyType.sale, status=PropertyStatus.active,
                                   district="Центр", rooms=2, price=100)
    page1 = await crud.list_properties(session, statuses=[PropertyStatus.active], limit=5, offset=0)
    page2 = await crud.list_properties(session, statuses=[PropertyStatus.active], limit=5, offset=5)
    assert len(page1) == 5 and len(page2) == 2

    found = await crud.search_properties(session, prop_type=PropertyType.sale, district="Центр",
                                         rooms=2, max_price_usd=150, limit=10)
    assert len(found) == 7


async def test_client_status_and_meeting_lifecycle(session):
    c = await crud.create_client(session, telegram_id=1, deal_type=ClientDealType.buy)
    p = await crud.create_property(session, property_kind=PropertyKind.apartment,
                                   type=PropertyType.sale, status=PropertyStatus.active)
    await crud.set_client_status(session, c.id, ClientStatus.contacted)
    m = await crud.create_meeting(session, client_id=c.id, property_id=p.id,
                                  when=timeutils.now() + dt.timedelta(days=1))
    assert m.id == "MTG_001"
    await crud.set_meeting_status(session, m.id, MeetingStatus.done)
    refreshed = await crud.get_client(session, c.id)
    assert refreshed.status == ClientStatus.contacted
    upcoming = await crud.upcoming_meetings(session)
    assert upcoming == []  # проведённая встреча не в списке предстоящих


async def test_due_for_bump_and_counts(session):
    old = timeutils.now() - dt.timedelta(days=5)
    p = await crud.create_property(session, property_kind=PropertyKind.apartment,
                                   type=PropertyType.rent, status=PropertyStatus.active,
                                   last_bump=old, price=1)
    due = await crud.properties_due_for_bump(session, timeutils.now() - dt.timedelta(days=3))
    assert [x.id for x in due] == [p.id]
    stats = await crud.count_properties_by_status(session)
    assert stats.get("active") == 1


async def test_reminder_window(session):
    c = await crud.create_client(session, telegram_id=1, deal_type=ClientDealType.buy)
    p = await crud.create_property(session, property_kind=PropertyKind.apartment,
                                   type=PropertyType.sale, status=PropertyStatus.active)
    now = timeutils.now()
    await crud.create_meeting(session, client_id=c.id, property_id=p.id, when=now + dt.timedelta(minutes=90))
    # окно "за 2 часа": (30м, 2ч]
    got = await crud.meetings_for_reminder(session, after=now + dt.timedelta(minutes=30),
                                           before=now + dt.timedelta(hours=2), flag="reminded_2h")
    assert len(got) == 1
    # окно "за 30 минут": (0, 30м] — встреча далеко, не попадает
    got30 = await crud.meetings_for_reminder(session, after=now,
                                             before=now + dt.timedelta(minutes=30), flag="reminded_30m")
    assert got30 == []
