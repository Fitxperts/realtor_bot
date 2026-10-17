"""Аналитика: события из настоящих сценариев, сводка за период, топ, недельный отчёт."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from bot.config import get_settings
from bot.database import crud
from bot.database.models import BotUser, Event, PropertyKind, PropertyStatus, PropertyType
from bot.services import analytics
from bot.utils import timeutils
from tests.conftest import FakeBot

ADMIN = 1
VISITOR = 820
OWNER = 821


async def _active(session, n: int = 1) -> list[str]:
    ids = []
    for i in range(n):
        prop = await crud.create_property(session, property_kind=PropertyKind.apartment, type=PropertyType.rent,
                                          status=PropertyStatus.active, district="Киргули", price=300 + i,
                                          currency="$")
        ids.append(prop.id)
    return ids


async def test_events_from_real_flows_and_admin_stats(make_harness):
    h = await make_harness(demo_mode=False)
    async with h.maker() as session:
        ids = await _active(session, 4)

    await h.message(VISITOR, "/start")
    await h.callback(VISITOR, "lang:ru")
    await h.callback(VISITOR, "cat:deal:all")                    # 3 просмотра в каталоге
    await h.callback(VISITOR, f"cat:lead:{ids[0]}")              # клик «Оставить заявку»
    await h.message(VISITOR, contact="+998901112233")            # заявка с телефоном
    await h.callback(VISITOR, "role:client")                     # анкета на подбор
    for text in ("аренда", "Киргули", "400$"):
        await h.message(VISITOR, text)
    await h.message(VISITOR, contact="+998901112233")
    await h.callback(VISITOR, "cl:confirm")

    await h.message(OWNER, "/start")                             # собственник размещает объект
    await h.callback(OWNER, "lang:ru")
    await h.callback(OWNER, "role:owner")
    await h.message(OWNER, "Сдаётся 2 комнатная квартира Фрунзе 2/4 этаж 250$ тел +998907776655")
    await h.callback(OWNER, "ow:pdone")
    await h.callback(OWNER, "ow:confirm")
    async with h.maker() as session:
        owner_prop = (await crud.list_owner_properties(session, OWNER))[0].id
    await h.callback(ADMIN, f"adm:approve:{owner_prop}")         # админ публикует

    async with h.maker() as session:
        counts = await analytics.summary(session, days=1)
    assert counts["catalog_view"] == 3 and counts["lead_click"] == 1 and counts["lead"] == 1
    assert counts["request"] == 1 and counts["listing"] == 1 and counts["published"] == 1
    assert counts["new_users"] == 2 and counts["visitors"] == 1

    h.clear()
    await h.message(ADMIN, "📊 Статистика")
    report = h.texts_to(ADMIN)[-1]
    assert "Активность за 7 дней" in report
    assert "Просмотры в каталоге: <b>3</b> (+3)" in report and "Заявки с телефоном: <b>1</b> (+1)" in report
    assert "клик → заявка 100%" in report
    # лидер топа — объект с заявкой (в каталоге его не показывали: там 3 самых новых)
    top = report.split("Топ объектов за 30 дней</b>\n", 1)[1].splitlines()
    assert top[0] == f"<code>{ids[0]}</code> — 👀 0 · ✍️ 1 · 🎯 1"
    assert f"<code>{ids[3]}</code> — 👀 1 · ✍️ 0 · 🎯 0" in top


async def _event(session, type_, *, days_ago: float, pid=None, tg=None):
    session.add(Event(type=type_, property_id=pid, telegram_id=tg,
                      created_at=timeutils.now() - dt.timedelta(days=days_ago)))
    await session.commit()


async def test_summary_period_and_delta(session):
    for days in (1, 2, 3):
        await _event(session, "lead", days_ago=days)
    await _event(session, "lead", days_ago=9)                    # прошлая неделя
    now = timeutils.now()
    cur = await analytics.summary(session, days=7, end=now)
    prev = await analytics.summary(session, days=7, end=now - dt.timedelta(days=7))
    assert cur["lead"] == 3 and prev["lead"] == 1
    text = analytics.format_report(cur, prev, [], "ru", title_key="an_title_7d")
    assert "Заявки с телефоном: <b>3</b> (+2)" in text
    assert "клик → заявка —" in text                            # кликов нет — без деления на ноль


async def test_top_orders_by_leads_then_clicks(session):
    for _ in range(5):
        await _event(session, "catalog_view", days_ago=1, pid="APT_A")
    await _event(session, "lead_click", days_ago=1, pid="APT_B")
    await _event(session, "lead", days_ago=1, pid="APT_B")
    await _event(session, "lead_click", days_ago=1, pid="APT_C")
    await _event(session, "lead", days_ago=40, pid="APT_D")      # за пределами 30 дней
    top = await analytics.top_properties(session, days=30)
    assert [row[0] for row in top] == ["APT_B", "APT_C", "APT_A"]
    assert top[0] == ("APT_B", 0, 1, 1)


async def test_track_never_breaks_flow(monkeypatch):
    def broken():
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(analytics, "get_sessionmaker", broken)
    await analytics.track("lead", telegram_id=1, property_id="APT_1")   # не бросает исключение


async def test_weekly_report_in_admin_language(make_harness):
    h = await make_harness()
    async with h.maker() as session:
        session.add(BotUser(telegram_id=ADMIN, role="admin", role_chosen=True, language="uz"))
        await session.commit()
        await _event(session, "lead", days_ago=1, pid="APT_X")
    await analytics.weekly_report(h.bot)
    report = h.texts_to(ADMIN)[-1]
    assert "Haftalik hisobot" in report and "Telefon bilan arizalar: <b>1</b>" in report


async def test_events_persist_after_property_removed(session):
    """Без внешних ключей: статистика не теряется, когда объект удалили (например, демо-сброс)."""
    await _event(session, "lead", days_ago=1, pid="APT_GONE")
    assert (await session.scalars(select(Event.property_id))).all() == ["APT_GONE"]


def test_weekly_job_registered():
    from bot.services.scheduler import setup_scheduler

    job = next(j for j in setup_scheduler(FakeBot()).get_jobs() if j.id == "weekly_report")
    assert "mon" in str(job.trigger) and get_settings().admin_ids
