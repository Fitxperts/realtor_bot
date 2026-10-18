"""Районы по городам: справочники, свой список агентства, каталог и разбор в другом городе."""
from __future__ import annotations

import pytest
from aiogram.methods import EditMessageReplyMarkup
from sqlalchemy import select

from bot import agency_config
from bot.database import crud
from bot.database.models import Agency, PropertyKind, PropertyStatus, PropertyType
from bot.keyboards import client_kb
from bot.services import geo, offline_parser
from bot.services.offline_parser import known_district
from bot.tenancy import agency_scope
from tests.flow import buttons

USER = 880
OWNER = 960


def test_parse_and_format_roundtrip():
    parsed = geo.parse("Чиланзар: chilonzor, Chilonzor, ч\n\n  Сергели  \nчиланзар: дубль\nQo'yliq: Куйлюк")
    assert parsed == (("Чиланзар", ("чиланзар", "chilonzor")), ("Сергели", ("сергели",)),
                      ("Qo'yliq", ("qoyliq", "куйлюк")))           # короткие варианты и повторы — мимо
    assert geo.parse(geo.format_(parsed)) == parsed
    assert len(geo.parse("\n".join(f"Массив {i}" for i in range(40)))) == geo.MAX_DISTRICTS
    assert geo.parse("   \n") == ()


def test_known_district_depends_on_city():
    assert known_district("2 xona Yunusobod 4-kvartal", geo.TASHKENT) == "Юнусабад"
    assert known_district("Мирзо Улугбекский район", geo.TASHKENT) == "Мирзо-Улугбек"
    assert known_district("2 xona Yunusobod 4-kvartal", geo.FERGANA) is None
    assert known_district("Qirg'uli bozor yonida", geo.FERGANA) == "Киргули"
    assert known_district("квартира в Сельмаше") == "Селмаш"          # вне агентства — Фергана


def _register(agency_id, city, districts=None):
    agency_config.register(agency_config.from_row(Agency(id=agency_id, name=f"A{agency_id}", city=city,
                                                         districts=districts, status="active")))


def test_offline_parser_uses_agency_city(make_harness):
    _register(2, "Ташкент")
    _register(3, "Наманган", "Давлатобод: davlatobod\nЧорсу")
    text = "Сдаётся 2 ком квартира, Chilonzor 9 kvartal, 400$, +998901234567"
    with agency_scope(2):
        assert offline_parser.parse(text)["district"] == "Чиланзар"
    with agency_scope(3):
        assert offline_parser.parse("Ijaraga 2 xonali, Davlatobod, 300$")["district"] == "Давлатобод"
    with agency_scope(1):
        assert offline_parser.parse("Ijaraga 2 xonali, Matalka, 300$")["district"] == "Маталка"
    agency_config._registry.pop(2), agency_config._registry.pop(3)


async def test_catalog_and_request_use_agency_districts(make_harness):
    h = await make_harness()
    bot2, api2 = h.add_agency_bot(2, "43:AGENCY2", city="Ташкент")
    async with h.maker() as session:
        with agency_scope(2):
            for district in ("Chilonzor 9", "Юнусобод", "Чиланзар-1"):
                await crud.create_property(session, property_kind=PropertyKind.apartment, type=PropertyType.rent,
                                           status=PropertyStatus.active, price=400, currency="$", district=district)
    await h.message(USER, "/start", bot=bot2)
    await h.callback(USER, "lang:ru", bot=bot2)
    await h.callback(USER, "cat:fd:rent:x:x:x", bot=bot2)
    edits = [c for c in api2.calls if isinstance(c, EditMessageReplyMarkup)]
    names = [text for text, _ in buttons(edits[-1])]
    assert "Чиланзар (2)" in names and "Юнусабад (1)" in names
    assert not any(n.startswith("Киргули") for n in names)              # ферганских массивов нет
    with agency_scope(2):
        kb_names = [b.text for row in client_kb.districts_kb("ru").inline_keyboard for b in row]
        assert kb_names[:2] == ["Бектемир", "Чиланзар"] and client_kb.get_district("11") == "Янгихаёт"
    with agency_scope(1):
        assert client_kb.get_district("2") == "Киргули"


@pytest.fixture
async def platform(make_harness, monkeypatch):
    from tests.test_platform import _Platform

    h = await make_harness()
    async with h.maker() as session:
        session.add_all([Agency(id=2, name="Наманган Уй", city="Наманган", owner_telegram_id=OWNER),
                         Agency(id=3, name="Тошкент Уй", city="Ташкент", owner_telegram_id=OWNER),
                         Agency(id=4, name="Чужое", city="Ташкент", owner_telegram_id=4004)])
        await session.commit()
        for row in await session.scalars(select(Agency).where(Agency.id > 1)):
            agency_config.register(agency_config.from_row(row))
    p = _Platform(h, monkeypatch)
    yield p
    p.detach()


async def test_districts_command_sets_and_resets(platform):
    p = platform
    await p.msg(OWNER, "/districts")
    assert ("🏢 Наманган Уй", "dist:a:2") in buttons(p.api.calls[-1])
    await p.cb(OWNER, "dist:a:2")
    assert "списка районов пока нет" in p.texts(OWNER)[-1]
    await p.msg(OWNER, "Давлатобод: davlatobod\nЧорсу\nЯнги Наманган: yangi namangan")
    assert "Сохранено — 3 массивов" in p.texts(OWNER)[-1]
    assert [n for n, _ in agency_config.cfg(2).districts] == ["Давлатобод", "Чорсу", "Янги Наманган"]  # сразу
    with agency_scope(2):
        assert known_district("ijaraga, Yangi Namangan") == "Янги Наманган"

    await p.cb(OWNER, "dist:a:3")                                        # Ташкент — встроенный справочник
    assert "районы/массивы (12)" in p.texts(OWNER)[-1]
    await p.msg(OWNER, "Чиланзар\nСергели")
    assert len(agency_config.cfg(3).districts) == 2
    await p.cb(OWNER, "dist:a:3")
    assert ("↩️ Справочник «Ташкент»", "dist:reset:3") in buttons(p.api.calls[-1])
    await p.cb(OWNER, "dist:reset:3")
    assert len(agency_config.cfg(3).districts) == 12
    async with p.h.maker() as session:
        assert (await session.get(Agency, 3)).districts is None


async def test_districts_only_own_agency(platform):
    p = platform
    await p.cb(OWNER, "dist:a:4")                                        # чужое — тишина
    await p.cb(OWNER, "dist:reset:4")
    assert not any("Чужое" in t for t in p.texts(OWNER))
    async with p.h.maker() as session:
        assert (await session.get(Agency, 4)).districts is None
    await p.msg(4004, "/districts")
    await p.msg(4004, "/pay")                                            # команда прерывает ввод списка
    await p.msg(4004, "Случайный текст")
    async with p.h.maker() as session:
        assert (await session.get(Agency, 4)).districts is None


async def test_onboarding_hints_districts_for_unknown_city(platform):
    from tests.test_platform import _onboard

    await _onboard(platform)                                             # город — Наманган
    await platform.cb(950, "pl:skipcontacts")
    await platform.cb(950, "pl:launch")
    assert any("/districts" in t for t in platform.texts(950))
