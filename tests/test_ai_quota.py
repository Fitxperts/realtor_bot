"""Лимит ИИ-разборов по тарифу: сверх лимита — офлайн-разбор, одно уведомление в месяц."""
from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from sqlalchemy import func, select

from bot.database.models import Event
from bot.services import ai_quota, billing
from bot.services.ai_service import AIService, get_ai_service
from bot.services.transcribe import get_transcribe_service
from bot.tenancy import agency_scope
from bot.utils import timeutils

ADMIN2 = 2003
USER = 870
AI_RESULT = {"deal_type": "rent", "property_kind": "apartment", "price": 300.0, "description": "ИИ-описание"}


@pytest.fixture
async def stand(make_harness, monkeypatch):
    h = await make_harness()
    monkeypatch.setitem(billing.PLANS, "start", {**billing.PLANS["start"], "ai_month": 2})
    monkeypatch.setattr(AIService, "enabled", property(lambda self: True))
    calls = []

    async def fake_ai(text):
        calls.append(text)
        return dict(AI_RESULT)

    monkeypatch.setattr(get_ai_service(), "_listing_with_ai", fake_ai)
    h.bot2, h.api2 = h.add_agency_bot(2, "43:AGENCY2", admin_ids=str(ADMIN2), plan="start",
                                      trial_until=timeutils.now() + dt.timedelta(days=5))
    h.ai_calls = calls
    return h


async def _count(h, type_, agency_id=2):
    async with h.maker() as session:
        with agency_scope(agency_id):
            return await session.scalar(select(func.count()).select_from(Event).where(Event.type == type_))


def _notices(api):
    return [c.text for c in api.calls if getattr(c, "chat_id", None) == ADMIN2 and "Лимит ИИ" in (c.text or "")]


async def test_over_limit_falls_back_offline_and_notifies_once(stand):
    h = stand
    text = "Сдаётся 2 ком. квартира, 300$, тел +998901112233"
    with agency_scope(2):
        first = await get_ai_service().parse_listing(text)
        second = await get_ai_service().parse_listing(text)
        third = await get_ai_service().parse_listing(text)          # лимит 2 исчерпан
        fourth = await get_ai_service().parse_listing(text)
    assert first["description"] == second["description"] == "ИИ-описание"
    assert third["description"] != "ИИ-описание" and third["price"] == 300 and third["deal_type"] == "rent"  # офлайн
    assert fourth["description"] != "ИИ-описание"
    assert len(h.ai_calls) == 2                                      # ИИ больше не вызывался
    assert await _count(h, "ai_parse") == 2
    notices = _notices(h.api2)
    assert len(notices) == 1 and "2 объявлений" in notices[0] and "/pay" in notices[0]


async def test_own_agency_unlimited(stand):
    h = stand
    with agency_scope(1):
        for _ in range(4):
            assert (await get_ai_service().parse_listing("Сдаётся квартира 300$"))["description"] == "ИИ-описание"
    assert await _count(h, "ai_parse", agency_id=1) == 0             # и не считается


async def test_last_month_does_not_count(stand):
    h = stand
    last_month = ai_quota.month_start() - dt.timedelta(days=3)
    async with h.maker() as session:
        with agency_scope(2):
            session.add_all([Event(type="ai_parse", created_at=last_month) for _ in range(5)])
            session.add(Event(type="ai_quota_notice", created_at=last_month))
            await session.commit()
    with agency_scope(2):
        assert await ai_quota.used() == 0 and await ai_quota.allow()
        assert (await get_ai_service().parse_listing("Сдаётся квартира 300$"))["description"] == "ИИ-описание"


async def test_plan_upgrade_raises_limit(stand, monkeypatch):
    from bot.agency_config import cfg, register

    h = stand
    async with h.maker() as session:
        with agency_scope(2):
            session.add_all([Event(type="ai_parse", created_at=timeutils.now()) for _ in range(2)])
            await session.commit()
    with agency_scope(2):
        assert not await ai_quota.allow()
        register(dataclasses.replace(cfg(), plan="pro"))              # оплатили «Про» — 600 в месяц
        assert ai_quota.limit() == billing.PLANS["pro"]["ai_month"] and await ai_quota.allow()


async def test_voice_refused_over_limit(stand, monkeypatch):
    h = stand
    monkeypatch.setattr(get_transcribe_service(), "is_enabled", lambda: True)

    async def must_not_transcribe(*_a, **_k):
        raise AssertionError("транскрипция сверх лимита")

    monkeypatch.setattr(get_transcribe_service(), "transcribe", must_not_transcribe)
    async with h.maker() as session:
        with agency_scope(2):
            session.add_all([Event(type="ai_parse", created_at=timeutils.now()) for _ in range(2)])
            await session.commit()
    await h.message(USER, "/start", bot=h.bot2)
    await h.callback(USER, "lang:ru", bot=h.bot2)
    await h.callback(USER, "role:owner", bot=h.bot2)
    await h.message(USER, voice="voice-file-1", bot=h.bot2)
    texts = [getattr(c, "text", "") or "" for c in h.api2.calls if getattr(c, "chat_id", None) == USER]
    assert any("Лимит распознавания" in t for t in texts)
    assert len(_notices(h.api2)) == 1
