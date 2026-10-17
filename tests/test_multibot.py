"""Мульти-бот: апдейт бота агентства N живёт в контексте N; вебхуки, задачи, токены."""
from __future__ import annotations

import datetime as dt

import pytest
from aiogram.methods import SendMessage
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
from sqlalchemy import select

from bot import bots, crypto
from bot.database import crud
from bot.database.models import Agency, BotUser, PropertyKind, PropertyStatus, PropertyType
from bot.tenancy import agency_scope, current_agency_id
from bot.web.agency_webhook import AgencyRequestHandler, agency_path
from tests.flow import FakeSession, buttons

USER = 840
ADMIN2 = 2001


@pytest.fixture
def secret_key(monkeypatch):
    monkeypatch.setenv("PLATFORM_SECRET_KEY", Fernet.generate_key().decode())


# ---------------------------------------------------------------------------
# Шифрование токенов
# ---------------------------------------------------------------------------
def test_token_roundtrip_and_errors(secret_key, monkeypatch):
    enc = crypto.encrypt("123:SECRET")
    assert "SECRET" not in enc and crypto.decrypt(enc) == "123:SECRET"
    monkeypatch.setenv("PLATFORM_SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        crypto.decrypt(enc)                                   # чужой ключ
    monkeypatch.delenv("PLATFORM_SECRET_KEY")
    with pytest.raises(crypto.SecretKeyMissing):
        crypto.encrypt("x")


async def test_load_agency_bots(session, secret_key):
    session.add_all([
        Agency(id=1, name="№1"),
        Agency(id=2, name="Два", bot_token_enc=crypto.encrypt("222:AAA"), webhook_secret="s2"),
        Agency(id=3, name="Отключено", status="disabled", bot_token_enc=crypto.encrypt("333:BBB")),
        Agency(id=4, name="Битый", bot_token_enc="мусор"),
    ])
    await session.commit()
    bots.clear()
    try:
        assert await bots.load_agency_bots(session) == [2]    # №1 — из BOT_TOKEN; 3 откл.; 4 битый
        assert bots.bot_for(2).id == 222 and bots.secret_for(2) == "s2"
        assert bots.agency_for_bot(222) == 2 and bots.agency_for_bot(999) == 1
    finally:
        bots.clear()


# ---------------------------------------------------------------------------
# Сквозной сценарий: два агентства в одном процессе
# ---------------------------------------------------------------------------
async def _setup(h):
    bot2, api2 = h.add_agency_bot(2, "43:AGENCY2", channel_id="@agency2_ch", admin_ids=str(ADMIN2),
                                  bot_username="agency2_bot")
    async with h.maker() as session:
        await crud.create_property(session, property_kind=PropertyKind.apartment, type=PropertyType.rent,
                                   status=PropertyStatus.active, district="Киргули", price=300, currency="$")
        with agency_scope(2):
            await crud.create_property(session, property_kind=PropertyKind.house, type=PropertyType.sale,
                                       status=PropertyStatus.active, district="Маталка", price=50_000,
                                       currency="$")
    return bot2, api2


async def test_two_agencies_in_one_process(make_harness):
    h = await make_harness()
    bot2, api2 = await _setup(h)

    # человек пишет боту агентства 2 — профиль, каталог и заявка там
    await h.message(USER, "/start", bot=bot2)
    await h.callback(USER, "lang:ru", bot=bot2)
    await h.callback(USER, "cat:deal:all", bot=bot2)
    cards = [c for c in api2.calls if any((d or "").startswith("cat:lead:") for _, d in buttons(c))]
    assert len(cards) == 1 and "Дом" in cards[0].text               # только объект агентства 2
    pid = buttons(cards[0])[0][1].split(":")[2]
    await h.callback(USER, f"cat:lead:{pid}", bot=bot2)
    await h.message(USER, contact="+998901112233", bot=bot2)

    to_admin2 = [c.text for c in api2.calls if isinstance(c, SendMessage) and c.chat_id == ADMIN2]
    assert any("Заявка по объекту" in t for t in to_admin2)          # админу агентства 2, его ботом
    assert h.texts_to(1) == []                                       # агентство №1 ничего не узнало
    assert not any(c for c in h.api.calls)                           # основной бот молчал

    async with h.maker() as session:
        rows = (await session.execute(
            select(BotUser.agency_id, BotUser.telegram_id).execution_options(all_agencies=True))).all()
        assert rows == [(2, USER)]
        with agency_scope(2):
            assert len(await crud.list_clients(session)) == 1
        assert await crud.list_clients(session) == []                # в №1 заявки нет


async def test_agency_admin_panel_is_own(make_harness):
    h = await make_harness()
    bot2, api2 = await _setup(h)
    await h.message(1, "📊 Статистика", bot=bot2)                     # админ №1 в боте №2 — не админ
    assert not any("Статистика" in (getattr(c, "text", "") or "") for c in api2.calls)
    await h.message(ADMIN2, "📊 Статистика", bot=bot2)
    assert any("Статистика" in (getattr(c, "text", "") or "") for c in api2.calls)


async def test_disabled_agency_is_silent(make_harness):
    h = await make_harness()
    bot3, api3 = h.add_agency_bot(3, "44:OFF", status="disabled")
    await h.message(USER, "/start", bot=bot3)
    assert api3.calls == []


# ---------------------------------------------------------------------------
# Вебхуки агентств
# ---------------------------------------------------------------------------
async def test_agency_webhook_routing(make_harness):
    h = await make_harness()
    bot2, api2 = h.add_agency_bot(2, "43:AGENCY2", webhook_secret="right-secret")
    app = web.Application()
    AgencyRequestHandler(dispatcher=h.dp, handle_in_background=False).register_routes(app, "/webhook")
    client = TestClient(TestServer(app))
    await client.start_server()
    update = {"update_id": 1, "message": {
        "message_id": 1, "date": int(dt.datetime.now().timestamp()), "text": "/start",
        "chat": {"id": USER, "type": "private"}, "from": {"id": USER, "is_bot": False, "first_name": "U"}}}
    try:
        ok = await client.post("/webhook/a2", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "right-secret"})
        assert ok.status == 200 and any(isinstance(c, SendMessage) for c in api2.calls)
        bad = await client.post("/webhook/a2", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
        assert bad.status == 401
        assert (await client.post("/webhook/a9", json=update)).status == 404     # нет такого агентства
        assert (await client.post("/webhook/a1", json=update)).status == 404     # №1 — на /webhook
    finally:
        await client.close()
    assert agency_path("/webhook", 1) == "/webhook" and agency_path("/webhook", 5) == "/webhook/a5"


# ---------------------------------------------------------------------------
# Задачи планировщика и подключение на ходу
# ---------------------------------------------------------------------------
async def test_for_each_agency_context_and_isolation(make_harness):
    h = await make_harness()
    bot2, _ = h.add_agency_bot(2, "43:AGENCY2")
    h.add_agency_bot(3, "44:OFF", status="disabled")
    seen = []

    async def job(bot, tag):
        seen.append((current_agency_id(), bot.id, tag))
        if bot.id == 42:
            raise RuntimeError("сбой у агентства №1")

    await bots.for_each_agency(job, "x")
    assert sorted(seen) == [(1, 42, "x"), (2, 43, "x")]                # отключённое пропущено, сбой не мешает


async def test_activate_new_agency_on_the_fly(monkeypatch):
    bots.clear()
    installed = []

    async def hook(agency_id, bot):
        installed.append((agency_id, bot.id))

    bots.set_activation_hook(hook)
    try:
        bot = await bots.activate(5, "555:NEW", "sec5")
        assert bots.bot_for(5) is bot and bots.secret_for(5) == "sec5" and installed == [(5, 555)]
        await bots.activate(5, "556:REPLACED", "sec6")                  # смена токена — старый бот убран
        assert bots.agency_for_bot(555) == 1 and bots.bot_for(5).id == 556
    finally:
        bots.set_activation_hook(None)
        bots.clear()


def test_fake_session_is_reusable():
    assert FakeSession().calls == []


@pytest.mark.parametrize("command", ["/backup", "/ai_status", "/ai_models groq", "/ai_eval"])
async def test_platform_commands_closed_to_agency_admins(make_harness, command):
    """Бэкап — это вся база всех агентств: админ агентства не должен его получить."""
    h = await make_harness()
    bot2, api2 = h.add_agency_bot(2, "43:AGENCY2", admin_ids=str(ADMIN2))
    await h.message(ADMIN2, command, bot=bot2)
    assert h.sent() == [] and [c for c in api2.calls if type(c).__name__.startswith("Send")] == []


async def test_platform_admin_keeps_commands_in_main_bot(make_harness, tmp_path, monkeypatch):
    from bot.config import get_settings

    h = await make_harness()
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path / "no_backups"))
    await h.message(1, "/backup")                                   # владелец платформы в основном боте
    assert any("Бэкапы не подключены" in t for t in h.texts_to(1))
