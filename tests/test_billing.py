"""Подписка: пробный период → оплачено → льгота → «только чтение»; напоминания; ограничения."""
from __future__ import annotations

import datetime as dt

from bot.agency_config import from_row, register
from bot.database import crud
from bot.database.models import Agency, PropertyKind, PropertyStatus, PropertyType
from bot.services import billing
from bot.services.billing import State, compute_state, extend, notice_key
from bot.tenancy import agency_scope
from bot.utils import timeutils
from tests.conftest import FakeBot

NOW = dt.datetime(2026, 10, 18, 12, 0)
D = dt.timedelta
ADMIN2 = 2001
USER = 860


def test_state_transitions():
    trial = NOW + D(days=5)
    assert compute_state(2, trial, None, now=NOW) == (State.trial, trial)
    assert compute_state(2, NOW - D(days=1), None, now=NOW)[0] == State.grace       # льгота 3 дня
    assert compute_state(2, NOW - D(days=4), None, now=NOW)[0] == State.readonly
    assert compute_state(2, NOW - D(days=40), NOW + D(days=10), now=NOW)[0] == State.active
    assert compute_state(2, None, None, created_at=NOW - D(days=3), now=NOW)[0] == State.trial  # старые агентства
    assert compute_state(1, None, None, now=NOW)[0] == State.active                  # своё агентство — всегда


def test_extend_from_now_or_from_current_end():
    assert extend(None, 1, now=NOW) == NOW + D(days=30)
    assert extend(NOW + D(days=10), 2, now=NOW) == NOW + D(days=70)                 # оплатил заранее — не теряет
    assert extend(NOW - D(days=5), 1, now=NOW) == NOW + D(days=30)


def test_notice_keys():
    assert notice_key(State.trial, NOW + D(days=10), now=NOW) is None
    assert notice_key(State.trial, NOW + D(days=3), now=NOW) == "trial_soon"
    assert notice_key(State.trial, NOW + D(hours=5), now=NOW) == "trial_last"
    assert notice_key(State.active, NOW + D(days=2), now=NOW) == "paid_soon"
    assert notice_key(State.grace, NOW + D(days=2), now=NOW) == "grace"
    assert notice_key(State.readonly, None, now=NOW) == "readonly"


async def test_reminder_sent_once_per_stage(session):
    session.add_all([Agency(id=1, name="№1"),
                     Agency(id=2, name="Два", admin_ids=str(ADMIN2), trial_until=timeutils.now() + D(days=2))])
    await session.commit()
    bot = FakeBot()
    with agency_scope(2):
        register(from_row(await session.get(Agency, 2)))
        assert await billing.send_reminder(bot, session) == "trial_soon"
        assert await billing.send_reminder(bot, session) is None                     # без повторов
    assert bot.messages[0][0] == ADMIN2 and "Пробный период заканчивается" in bot.messages[0][1]
    assert "/pay" in bot.messages[0][1]
    with agency_scope(1):
        assert await billing.send_reminder(bot, session) is None                     # своё агентство — без оплаты


async def test_readonly_agency_keeps_leads_but_blocks_listings(make_harness):
    h = await make_harness()
    expired = timeutils.now() - D(days=10)
    bot2, api2 = h.add_agency_bot(2, "43:AGENCY2", admin_ids=str(ADMIN2), trial_until=expired)
    async with h.maker() as session:
        with agency_scope(2):
            pending = await crud.create_property(session, property_kind=PropertyKind.apartment,
                                                 type=PropertyType.rent, status=PropertyStatus.pending, price=300)
            active = await crud.create_property(session, property_kind=PropertyKind.apartment,
                                                type=PropertyType.rent, status=PropertyStatus.active, price=300)

    def texts():
        return [getattr(c, "text", None) or getattr(c, "caption", None) or "" for c in api2.calls]

    await h.message(USER, "/start", bot=bot2)
    await h.callback(USER, "lang:ru", bot=bot2)
    await h.callback(USER, "cat:deal:all", bot=bot2)                                # каталог работает
    assert any(active.id in t for t in texts())
    await h.callback(USER, f"cat:lead:{active.id}", bot=bot2)
    await h.message(USER, contact="+998901112233", bot=bot2)                       # и заявки тоже
    assert any("Заявка по объекту" in getattr(c, "text", "") for c in api2.calls if getattr(c, "chat_id", 0) == ADMIN2)

    await h.callback(USER, "role:owner", bot=bot2)                                  # а приём объявлений — нет
    assert any("Приём объявлений временно приостановлен" in t for t in texts())

    await h.callback(ADMIN2, f"adm:approve:{pending.id}", bot=bot2)                 # и публикация тоже
    answer = [c for c in api2.calls if type(c).__name__ == "AnswerCallbackQuery"][-1]
    assert answer.show_alert and "Подписка не оплачена" in answer.text
    async with h.maker() as session:
        with agency_scope(2):
            assert (await crud.get_property(session, pending.id)).status == PropertyStatus.pending


async def test_own_agency_never_readonly(make_harness):
    h = await make_harness()
    await h.message(USER, "/start")
    await h.callback(USER, "lang:ru")
    await h.callback(USER, "role:owner")
    texts = h.texts_to(USER)
    assert not any("Приём объявлений временно приостановлен" in t for t in texts)
    assert any("одним сообщением" in t for t in texts)                           # форма открылась
