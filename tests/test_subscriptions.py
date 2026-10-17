"""Подписки на поиск: деньги с валютой, подбор в $, антиспам, сквозной сценарий."""
from __future__ import annotations

import pytest
from aiogram.methods import AnswerCallbackQuery
from sqlalchemy import func, select

from bot.config import get_settings
from bot.database import crud
from bot.database.models import (
    BotUser,
    Client,
    ClientDealType,
    PropertyKind,
    PropertyStatus,
    PropertyType,
    SearchAlert,
)
from bot.services import alerts, matcher, publisher
from bot.utils.money import parse_money
from tests.conftest import FakeBot
from tests.flow import buttons

USER = 800


@pytest.mark.parametrize("text,expected", [
    ("400$", (400, "$")), ("$400", (400, "$")), ("до 400 у.е.", (400, "$")), ("400 dollar", (400, "$")),
    ("400", (400, "$")),                       # без валюты и меньше 10 000 — доллары
    ("3 млн", (3_000_000, "сум")), ("3.5 млн", (3_500_000, "сум")), ("3,5 mln so'm", (3_500_000, "сум")),
    ("4 лям", (4_000_000, "сум")), ("300 ming", (300_000, "сум")), ("3 500 000", (3_500_000, "сум")),
    ("45.000 $", (45_000, "$")), ("67.000", (67_000, "сум")), ("300-400$", (400, "$")),
    ("от 300 до 400", (400, "$")), ("50 тыс $", (50_000, "$")),
    ("abc", None), ("0", None), ("", None),
])
def test_parse_money(text, expected):
    result = parse_money(text)
    assert (result if result is None else (round(result[0]), result[1])) == expected


async def _client(session, tg=USER, **kw):
    kw.setdefault("deal_type", ClientDealType.rent)
    kw.setdefault("district", "Киргули")
    return await crud.create_client(session, telegram_id=tg, **kw)


async def _prop(session, price, currency, **kw):
    kw.setdefault("type", PropertyType.rent)
    kw.setdefault("district", "Киргули")
    return await crud.create_property(session, property_kind=PropertyKind.apartment, status=PropertyStatus.active,
                                      price=price, currency=currency, **kw)


async def test_matching_compares_in_dollars(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "usd_rate", 12_000)
    await _client(session, budget=400, currency="$")
    cheap = await _prop(session, 3_500_000, "сум")      # ≈ $292 — подходит
    pricey = await _prop(session, 5_500_000, "сум")     # ≈ $458 > $440 (бюджет +10%)
    assert [c.telegram_id for c in await matcher.clients_for_property(session, cheap)] == [USER]
    assert await matcher.clients_for_property(session, pricey) == []

    client = (await session.scalars(select(Client))).one()
    assert [p.id for p in await matcher.find_for_client(session, client)] == [cheap.id]


async def test_sum_budget_does_not_match_dollar_house(session):
    await _client(session, deal_type=ClientDealType.buy, budget=3_500_000, currency="сум")   # ≈ $292
    house = await _prop(session, 55_000, "$", type=PropertyType.sale)
    assert await matcher.clients_for_property(session, house) == []   # раньше: 55000 < 3500000 → «подходит»


async def _alerts_count(session, tg=USER) -> int:
    return int(await session.scalar(select(func.count()).select_from(SearchAlert)
                                    .where(SearchAlert.telegram_id == tg)))


async def test_alerts_once_per_property_and_daily_cap(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "alerts_daily_limit", 2)
    await _client(session, budget=500, currency="$")
    await _client(session, budget=600, currency="$")    # вторая заявка того же человека
    bot = FakeBot()
    p1 = await _prop(session, 300, "$")
    assert await alerts.send_property_alerts(bot, session, p1) == 1   # две заявки — одно уведомление
    assert await alerts.send_property_alerts(bot, session, p1) == 0   # повторно не шлём
    await alerts.send_property_alerts(bot, session, await _prop(session, 310, "$"))
    assert await alerts.send_property_alerts(bot, session, await _prop(session, 320, "$")) == 0  # лимит 2/сутки
    assert await _alerts_count(session) == 2


async def test_no_alert_to_owner_or_unsubscribed(session):
    owner = await _client(session, tg=901, budget=500, currency="$")
    off = await _client(session, tg=902, budget=500, currency="$")
    off.notify = False
    await session.commit()
    prop = await _prop(session, 300, "$", owner_telegram_id=owner.telegram_id)
    assert await alerts.send_property_alerts(FakeBot(), session, prop) == 0


async def test_alert_in_client_language_with_buttons(session):
    session.add(BotUser(telegram_id=USER, role="client", role_chosen=True, language="uz"))
    await session.commit()
    client = await _client(session, budget=500, currency="$")
    bot = FakeBot()
    prop = await _prop(session, 300, "$")
    await alerts.send_property_alerts(bot, session, prop)
    chat, text = bot.messages[-1]
    assert chat == USER and "Soʻrovingiz boʻyicha yangi obyekt" in text
    data = [b.callback_data for row in bot.markups[-1].inline_keyboard for b in row]
    assert data == [f"cat:lead:{prop.id}", f"sub:off:{client.id}"]


# ---------------------------------------------------------------------------
# Сквозной сценарий через настоящий Dispatcher
# ---------------------------------------------------------------------------
async def _publish(h, price, currency="$"):
    async with h.maker() as session:
        prop = await crud.create_property(
            session, property_kind=PropertyKind.apartment, type=PropertyType.rent, status=PropertyStatus.pending,
            district="Киргули", rooms=2, price=price, currency=currency)
        await publisher.publish_property(h.bot, session, prop.id)
        return prop.id


async def test_subscription_flow(make_harness):
    h = await make_harness(demo_mode=False)
    await h.message(USER, "/start")
    await h.callback(USER, "lang:ru")
    await h.callback(USER, "role:client")
    await h.message(USER, "аренда")
    await h.message(USER, "Киргули")
    await h.message(USER, "до 400$")
    await h.message(USER, contact="+998901234567")
    assert "400 $" in h.texts_to(USER)[-1]                     # превью: бюджет с валютой
    await h.callback(USER, "cl:confirm")
    assert any("Подписка включена" in t for t in h.texts_to(USER))

    # новый подходящий объект в сумах (≈ $292) → уведомление с кнопками
    pid = await _publish(h, 3_500_000, "сум")
    alert = h.last_to(USER)
    assert "Новый объект по вашему запросу" in (getattr(alert, "caption", None) or alert.text)
    assert [d for _, d in buttons(alert)][0] == f"cat:lead:{pid}"
    unsub = [d for _, d in buttons(alert)][1]

    # «Мои подписки» показывает запрос
    await h.message(USER, "🔔 Мои подписки")
    listing = [t for t in h.texts_to(USER) if "Аренда" in t and "Киргули" in t]
    assert listing and "до 400 $" in listing[-1]

    # отписка → новые объекты больше не приходят
    await h.callback(USER, unsub)
    answers = [c for c in h.api.calls if isinstance(c, AnswerCallbackQuery)]
    assert "Отписались" in answers[-1].text and answers[-1].show_alert
    before = len(h.sent(USER))
    await _publish(h, 3_000_000, "сум")
    assert len(h.sent(USER)) == before
    await h.message(USER, "🔔 Мои подписки")
    assert "нет подписок" in h.texts_to(USER)[-1]


async def test_lead_offers_similar_and_opt_in(make_harness):
    h = await make_harness(demo_mode=False)
    await h.message(USER, "/start")
    await h.callback(USER, "lang:ru")
    pid = await _publish(h, 300)
    await h.callback(USER, f"cat:lead:{pid}")
    await h.message(USER, contact="+998901234567")
    offer = h.last_to(USER)
    assert offer.text == "Прислать похожие объекты, когда появятся?"
    async with h.maker() as session:
        client = (await session.scalars(select(Client).where(Client.telegram_id == USER))).one()
        assert client.notify is False and client.currency == "$"   # валюта объекта, а не «сум»
    await h.callback(USER, buttons(offer)[0][1])
    async with h.maker() as session:
        assert (await session.get(Client, client.id)).notify is True
    await _publish(h, 280)                                        # похожий → теперь приходит
    assert "Новый объект по вашему запросу" in (h.last_to(USER).text or "")


async def test_cannot_unsubscribe_someone_else(make_harness):
    h = await make_harness(demo_mode=False)
    async with h.maker() as session:
        victim = await _client(session, tg=950, budget=500, currency="$")
    await h.callback(USER, f"sub:off:{victim.id}")
    async with h.maker() as session:
        assert (await session.get(Client, victim.id)).notify is True
