"""Каталог: выдача, фильтры (сделка, район, комнаты, цена) и панель фильтра в боте."""
from __future__ import annotations

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, SendPhoto

from bot.config import get_settings
from bot.database import crud
from bot.database.models import PropertyKind, PropertyStatus, PropertyType
from bot.services import demo
from bot.services.catalog_filter import DISTRICTS, CatalogFilter, district_counts, filtered
from tests.flow import buttons

USER = 830
KIRGULI = DISTRICTS.index("Киргули")


async def _mk(session, **kw):
    kw.setdefault("property_kind", PropertyKind.apartment)
    kw.setdefault("currency", "сум")
    kw.setdefault("status", PropertyStatus.active)
    return await crud.create_property(session, **kw)


# ---------------------------------------------------------------------------
# Сервис фильтра
# ---------------------------------------------------------------------------
async def test_only_active_and_by_deal(session):
    await _mk(session, type=PropertyType.rent, price=1)
    await _mk(session, type=PropertyType.sale, price=2)
    await _mk(session, type=PropertyType.rent, price=3, status=PropertyStatus.pending)  # не в каталоге
    assert len(await filtered(session, CatalogFilter())) == 2
    assert len(await filtered(session, CatalogFilter("rent"))) == 1


def test_encode_decode_and_garbage():
    f = CatalogFilter("rent", KIRGULI, 4, 1)
    assert CatalogFilter.decode(f.encode()) == f
    assert CatalogFilter.decode("zzz:99:7:42") == CatalogFilter()          # мусор → «без фильтра»
    assert CatalogFilter("rent", price=2).with_(deal="all").price is None   # у «все» нет шкалы цен
    assert len("cat:s:" + CatalogFilter("sale", 9, 4, 5).encode() + ":120") <= 64   # лимит Telegram


async def test_district_by_any_spelling_rooms_and_price(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "usd_rate", 12_000)
    a = await _mk(session, type=PropertyType.rent, district="Qirg'uli", rooms=2, price=3_500_000)  # ≈ $292
    await _mk(session, type=PropertyType.rent, district="Фрунзе", rooms=2, price=250, currency="$")
    big = await _mk(session, type=PropertyType.sale, district="Киргули", rooms=5, price=90_000, currency="$")
    assert [p.id for p in await filtered(session, CatalogFilter("rent", district=KIRGULI))] == [a.id]
    assert [p.id for p in await filtered(session, CatalogFilter("all", rooms=4))] == [big.id]      # 4+
    assert {p.id for p in await filtered(session, CatalogFilter("rent", price=1))} >= {a.id}       # до $300
    assert await filtered(session, CatalogFilter("rent", price=0)) == []                          # до $200 — нет


async def test_district_counts_respect_other_conditions(session):
    await _mk(session, type=PropertyType.rent, district="Киргули", rooms=1, price=1)
    await _mk(session, type=PropertyType.rent, district="Киргули", rooms=2, price=1)
    await _mk(session, type=PropertyType.rent, district="Маталка", rooms=2, price=1)
    await _mk(session, type=PropertyType.sale, district="Фрунзе", rooms=2, price=1)
    counts = await district_counts(session, CatalogFilter("rent", rooms=2))
    assert [(name, n) for _, name, n in counts] == [("Киргули", 1), ("Маталка", 1)]   # Фрунзе — продажа


# ---------------------------------------------------------------------------
# Сквозные сценарии (настоящий Dispatcher, 18 примеров из демо)
# ---------------------------------------------------------------------------
async def _harness(make_harness):
    h = await make_harness()
    async with h.maker() as session:
        await demo.ensure_seed(session)
    await h.message(USER, "/start")
    await h.callback(USER, "lang:ru")
    h.clear()
    return h


def _last(h, kind):
    return [c for c in h.api.calls if isinstance(c, kind)][-1]


async def test_filter_panel_flow(make_harness):
    h = await _harness(make_harness)
    await h.message(USER, "📋 Объявления")
    assert ("🔎 Фильтр", "cat:f:all:x:x:x") in buttons(h.last_to(USER))

    await h.callback(USER, "cat:f:all:x:x:x")
    assert "Найдено: <b>18</b>" in h.last_to(USER).text

    await h.callback(USER, "cat:fp:rent:x:x:x")                        # только аренда
    assert "Найдено: <b>7</b>" in _last(h, EditMessageText).text

    await h.callback(USER, "cat:fd:rent:x:x:x")                        # районы — только где есть аренда
    districts = [text for text, _ in buttons(_last(h, EditMessageReplyMarkup))]
    assert "Киргули (1)" in districts and not any(t.startswith("Маталка") for t in districts)

    await h.callback(USER, f"cat:fp:rent:{KIRGULI}:x:x")
    await h.callback(USER, f"cat:fx:rent:{KIRGULI}:x:x")
    prices = [text for text, _ in buttons(_last(h, EditMessageReplyMarkup))]
    assert "до $300" in prices and "до $70 000" not in prices          # шкала аренды

    await h.callback(USER, f"cat:fp:rent:{KIRGULI}:x:1")
    panel = _last(h, EditMessageText)
    assert "Район: Киргули" in panel.text and "до $300" in panel.text and "Найдено: <b>1</b>" in panel.text
    show = next(data for text, data in buttons(panel) if text.startswith("✅"))
    h.clear()
    await h.callback(USER, show)
    cards = [m for m in h.sent(USER) if isinstance(m, SendPhoto)]
    assert len(cards) == 1 and "APT_1001" in cards[0].caption


async def test_rooms_four_plus_and_paging_keeps_filter(make_harness):
    h = await _harness(make_harness)
    await h.callback(USER, "cat:s:sale:x:4:x:0")                       # продажа, 4+ комнат
    cards = [m.caption for m in h.sent(USER) if isinstance(m, SendPhoto)]
    assert len(cards) == 3 and all(any(pid in c for pid in ("APT_1008", "HSE_1001", "HSE_1002")) for c in cards)
    assert "Это все объявления" in h.texts_to(USER)[-1]

    h.clear()
    await h.callback(USER, "cat:s:sale:x:x:x:0")                       # 11 объектов → «показать ещё»
    more = next(data for text, data in buttons(h.last_to(USER)) if text.startswith("⬇️"))
    assert more == "cat:s:sale:x:x:x:3"


async def test_empty_filter_offers_change_and_request(make_harness):
    h = await _harness(make_harness)
    await h.callback(USER, f"cat:s:rent:{DISTRICTS.index('Маталка')}:x:x:0")
    last = h.last_to(USER)
    assert "По этому фильтру пока ничего нет" in last.text
    assert [data for _, data in buttons(last)] == [f"cat:f:rent:{DISTRICTS.index('Маталка')}:x:x", "cat:req"]


async def test_price_needs_deal(make_harness):
    h = await _harness(make_harness)
    await h.callback(USER, "cat:fx:all:x:x:x")
    answer = _last(h, AnswerCallbackQuery)
    assert answer.show_alert and "аренду или покупку" in answer.text


@pytest.mark.parametrize("data", ["cat:deal:rent", "cat:more:rent:3"])
async def test_old_buttons_still_work(make_harness, data):
    h = await _harness(make_harness)
    await h.callback(USER, data)
    assert any(isinstance(m, SendPhoto) for m in h.sent(USER))
