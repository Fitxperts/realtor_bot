"""Проверка актуальности: вопрос через 14 дней, снятие через 3 дня без ответа, ответы кнопками."""
from __future__ import annotations

import datetime as dt

from aiogram.methods import AnswerCallbackQuery, EditMessageCaption, EditMessageText, SendMessage

from bot.config import get_settings
from bot.database import crud
from bot.database.models import PropertyKind, PropertyStatus, PropertyType
from bot.services import freshness
from bot.utils import timeutils
from tests.conftest import FakeBot

OWNER = 600
ADMIN = 1


def _days_ago(days: float) -> dt.datetime:
    return timeutils.now() - dt.timedelta(days=days)


async def _prop(session, *, age=15, owner=OWNER, **kw):
    kw.setdefault("status", PropertyStatus.active)
    prop = await crud.create_property(session, property_kind=PropertyKind.apartment, type=PropertyType.rent,
                                      district="Киргули", price=300, currency="$", owner_telegram_id=owner, **kw)
    prop.created_at = _days_ago(age)
    await session.commit()
    return prop


def _data(bot: FakeBot) -> list[list[str]]:
    return [[b.callback_data for row in m.inline_keyboard for b in row] for m in bot.markups if m]


async def test_asks_owner_after_14_days_once(session):
    old = await _prop(session, age=15)
    await _prop(session, age=10)                          # ещё рано
    bot = FakeBot()
    assert await freshness.ask_owners(bot, session) == 1
    chat, text = bot.messages[0]
    assert chat == OWNER and old.id in text and "ещё актуально" in text
    assert _data(bot) == [[f"fr:yes:{old.id}", f"fr:sold:{old.id}"]]
    assert old.freshness_asked_at is not None
    assert await freshness.ask_owners(bot, session) == 0  # второй раз не спрашиваем


async def test_confirmed_recently_not_asked(session):
    prop = await _prop(session, age=40)
    prop.confirmed_at = _days_ago(3)
    await session.commit()
    assert await freshness.ask_owners(FakeBot(), session) == 0


async def test_autorepost_listing_asks_admins(session):
    prop = await _prop(session, owner=None)
    bot = FakeBot()
    await freshness.ask_owners(bot, session)
    assert [chat for chat, _ in bot.messages] == get_settings().admin_ids and prop.id in bot.messages[0][1]


async def test_cap_per_recipient(session):
    for i in range(7):
        await _prop(session, age=20 + i)
    bot = FakeBot()
    assert await freshness.ask_owners(bot, session) == freshness.ASK_PER_RECIPIENT
    assert await freshness.ask_owners(bot, session) == 2   # остальные — на следующий день
    assert len(bot.messages) == 7


async def test_archives_after_3_days_without_answer(session):
    stale = await _prop(session, channel_post_id=55)
    stale.freshness_asked_at = _days_ago(4)
    fresh_q = await _prop(session)
    fresh_q.freshness_asked_at = _days_ago(2)              # ещё ждём
    answered = await _prop(session)
    answered.freshness_asked_at, answered.confirmed_at = _days_ago(5), _days_ago(4)
    await session.commit()

    bot = FakeBot()
    assert await freshness.archive_unanswered(bot, session) == 1
    assert stale.status == PropertyStatus.archived
    assert fresh_q.status == PropertyStatus.active and answered.status == PropertyStatus.active
    assert ("edit_caption", 55) in bot.calls or ("edit_text", 55) in bot.calls   # пост помечен
    chat, text = bot.messages[-1]
    assert chat == OWNER and "снято с публикации" in text
    assert _data(bot)[-1] == [f"fr:yes:{stale.id}"]       # можно вернуть


async def test_waiting_answer_is_not_bumped(session):
    waiting = await _prop(session)
    waiting.freshness_asked_at = _days_ago(1)
    other = await _prop(session)
    await session.commit()
    due = await crud.properties_due_for_bump(session, timeutils.now())
    assert [p.id for p in due] == [other.id]


# ---------------------------------------------------------------------------
# Ответы кнопками (настоящий Dispatcher)
# ---------------------------------------------------------------------------
async def _setup(h, **kw):
    async with h.maker() as session:
        prop = await _prop(session, **kw)
        prop.freshness_asked_at = _days_ago(1)
        await session.commit()
        return prop.id


async def _get(h, pid):
    async with h.maker() as session:
        return await crud.get_property(session, pid)


async def test_owner_confirms(make_harness):
    h = await make_harness()
    pid = await _setup(h)
    await h.callback(OWNER, f"fr:yes:{pid}")
    prop = await _get(h, pid)
    assert prop.status == PropertyStatus.active and prop.confirmed_at and prop.freshness_asked_at is None
    answer = [c for c in h.api.calls if isinstance(c, AnswerCallbackQuery)][-1]
    assert "остаётся в каталоге" in answer.text


async def test_owner_restores_archived(make_harness):
    h = await make_harness()
    pid = await _setup(h, status=PropertyStatus.archived)
    await h.callback(OWNER, f"fr:yes:{pid}")
    prop = await _get(h, pid)
    assert prop.status == PropertyStatus.active and prop.channel_post_id   # опубликован заново
    assert any(isinstance(c, SendMessage) and c.chat_id == "@demo_channel" for c in h.api.calls)


async def test_owner_marks_sold_and_admins_told(make_harness):
    h = await make_harness()
    pid = await _setup(h, channel_post_id=77)
    await h.callback(OWNER, f"fr:sold:{pid}")
    assert (await _get(h, pid)).status == PropertyStatus.rented
    assert any(isinstance(c, EditMessageCaption | EditMessageText) for c in h.api.calls)
    assert any(pid in t and "сданный/проданный" in t for t in h.texts_to(ADMIN))


async def test_stranger_cannot_answer(make_harness):
    h = await make_harness()
    pid = await _setup(h)
    await h.callback(999, f"fr:sold:{pid}")
    assert (await _get(h, pid)).status == PropertyStatus.active


async def test_admin_answers_for_autorepost(make_harness):
    h = await make_harness()
    pid = await _setup(h, owner=None)
    await h.callback(ADMIN, f"fr:yes:{pid}")
    assert (await _get(h, pid)).confirmed_at is not None


def test_scheduler_has_freshness_only_outside_demo(monkeypatch):
    from bot.services.scheduler import setup_scheduler

    monkeypatch.setattr(get_settings(), "demo_mode", False)
    assert "freshness" in {j.id for j in setup_scheduler(object()).get_jobs()}
    monkeypatch.setattr(get_settings(), "demo_mode", True)
    assert "freshness" not in {j.id for j in setup_scheduler(object()).get_jobs()}
