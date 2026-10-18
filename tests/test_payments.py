"""Оплата подписки: Payme Merchant API и Click SHOP-API → продление agencies.paid_until."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from bot import agency_config
from bot.database.models import Agency, Payment
from bot.services import payments
from bot.utils import timeutils

D = dt.timedelta
ADMIN2 = 2002
PAYME_KEY = "payme-secret-key"
CLICK_SECRET = "click-secret"
AUTH = "Basic " + base64.b64encode(f"Paycom:{PAYME_KEY}".encode()).decode()


@pytest.fixture
def env(monkeypatch):
    for name, value in {"PAYME_MERCHANT_ID": "5e730e8e0b852a417aa49ceb", "PAYME_KEY": PAYME_KEY,
                        "CLICK_SERVICE_ID": "111", "CLICK_MERCHANT_ID": "222", "CLICK_SECRET_KEY": CLICK_SECRET,
                        }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
async def stand(make_harness, env):
    """Агентство №2 со своим ботом, пробный период ещё идёт; счёт «Про» × 3 мес."""
    h = await make_harness()
    trial = timeutils.now() + D(days=5)
    bot2, api2 = h.add_agency_bot(2, "43:AGENCY2", admin_ids=str(ADMIN2), trial_until=trial)
    async with h.maker() as session:
        session.add(Agency(id=2, name="Два", admin_ids=str(ADMIN2), trial_until=trial, plan="start"))
        await session.commit()
        invoice = await payments.create_invoice(session, 2, "pro", 3, payer_id=ADMIN2)
    h.invoice, h.api2 = invoice, api2
    return h


async def _agency(h) -> Agency:
    async with h.maker() as session:
        return await session.get(Agency, 2)


async def _rpc(h, method, params, auth=AUTH, rid=1):
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode()
    return await payments.payme_handle(h.maker, auth, body)


def _texts(api, chat_id):
    return [getattr(c, "text", "") or "" for c in api.calls if getattr(c, "chat_id", None) == chat_id]


# ---------------------------------------------------------------------------
# Payme
# ---------------------------------------------------------------------------
async def test_payme_full_cycle_extends_once(stand):
    h, inv = stand, stand.invoice
    assert inv.amount == 590_000 * 3
    account, amount = {"order_id": str(inv.id)}, inv.amount * 100        # Payme считает в тийинах
    assert (await _rpc(h, "CheckPerformTransaction", {"amount": amount, "account": account}))["result"] == \
        {"allow": True}
    created = await _rpc(h, "CreateTransaction", {"id": "pm-1", "time": payments.now_ms(), "amount": amount,
                                                  "account": account})
    assert created["result"]["state"] == 1 and created["result"]["transaction"] == str(inv.id)
    again = await _rpc(h, "CreateTransaction", {"id": "pm-1", "time": payments.now_ms(), "amount": amount,
                                                "account": account})
    assert again["result"] == created["result"]                         # повтор — тот же ответ

    before = timeutils.now()
    done = await _rpc(h, "PerformTransaction", {"id": "pm-1"})
    assert done["result"]["state"] == 2 and done["result"]["perform_time"] > 0
    row = await _agency(h)
    assert row.plan == "pro" and before + D(days=90) <= row.paid_until <= timeutils.now() + D(days=90)
    assert agency_config.cfg(2).plan == "pro" and agency_config.cfg(2).paid_until == row.paid_until  # на ходу
    assert any("Оплата получена" in t and "«Про», 3 мес" in t for t in _texts(h.api2, ADMIN2))
    assert any("💰 Оплата (payme)" in t and "1 770 000 сум" in t for t in _texts(h.api, 1))

    repeat = await _rpc(h, "PerformTransaction", {"id": "pm-1"})
    assert repeat["result"] == done["result"]
    assert (await _agency(h)).paid_until == row.paid_until               # второго продления нет

    check = await _rpc(h, "CheckTransaction", {"id": "pm-1"})
    assert check["result"]["state"] == 2 and check["result"]["cancel_time"] == 0 and check["result"]["reason"] is None

    # Оплата по закрытому счёту невозможна
    closed = await _rpc(h, "CheckPerformTransaction", {"amount": amount, "account": account})
    assert closed["error"]["code"] == -31008


async def test_payme_refund_rolls_back(stand):
    h, inv = stand, stand.invoice
    account, amount = {"order_id": str(inv.id)}, inv.amount * 100
    await _rpc(h, "CreateTransaction", {"id": "pm-2", "time": payments.now_ms(), "amount": amount, "account": account})
    await _rpc(h, "PerformTransaction", {"id": "pm-2"})
    paid = (await _agency(h)).paid_until
    cancel = await _rpc(h, "CancelTransaction", {"id": "pm-2", "reason": 5})
    assert cancel["result"]["state"] == -2 and cancel["result"]["cancel_time"] > 0
    assert (await _agency(h)).paid_until == paid - D(days=90)
    again = await _rpc(h, "CancelTransaction", {"id": "pm-2", "reason": 5})
    assert again["result"] == cancel["result"]                           # повтор — без второго отката
    assert (await _agency(h)).paid_until == paid - D(days=90)
    assert (await _rpc(h, "CheckTransaction", {"id": "pm-2"}))["result"]["reason"] == 5
    assert any("возвращена" in t for t in _texts(h.api2, ADMIN2))


async def test_payme_cancel_before_perform_then_perform_refused(stand):
    h, inv = stand, stand.invoice
    account, amount = {"order_id": str(inv.id)}, inv.amount * 100
    await _rpc(h, "CreateTransaction", {"id": "pm-3", "time": payments.now_ms(), "amount": amount, "account": account})
    assert (await _rpc(h, "CancelTransaction", {"id": "pm-3", "reason": 3}))["result"]["state"] == -1
    assert (await _rpc(h, "PerformTransaction", {"id": "pm-3"}))["error"]["code"] == -31008
    assert (await _agency(h)).paid_until is None


async def test_payme_validation_errors(stand):
    h, inv = stand, stand.invoice
    account, amount = {"order_id": str(inv.id)}, inv.amount * 100
    bad_auth = "Basic " + base64.b64encode(b"Paycom:wrong").decode()
    assert (await _rpc(h, "CheckPerformTransaction", {"amount": amount, "account": account}, auth=bad_auth)
            )["error"]["code"] == -32504
    assert (await _rpc(h, "CheckPerformTransaction", {"amount": amount, "account": account}, auth=None)
            )["error"]["code"] == -32504
    wrong_sum = await _rpc(h, "CheckPerformTransaction", {"amount": inv.amount, "account": account})  # суммы, не тийины
    assert wrong_sum["error"]["code"] == -31001
    unknown = await _rpc(h, "CheckPerformTransaction", {"amount": amount, "account": {"order_id": "999999"}})
    assert unknown["error"]["code"] == -31050 and unknown["error"]["data"] == "order_id"
    assert set(unknown["error"]["message"]) == {"ru", "uz", "en"}
    assert (await _rpc(h, "CheckTransaction", {"id": "nope"}))["error"]["code"] == -31003
    assert (await _rpc(h, "Foo", {}))["error"]["code"] == -32601
    parse = await payments.payme_handle(h.maker, AUTH, b"{not json")
    assert parse["error"]["code"] == -32700

    # по счёту уже идёт одна транзакция — вторая (другой id) не создаётся
    await _rpc(h, "CreateTransaction", {"id": "pm-a", "time": payments.now_ms(), "amount": amount, "account": account})
    other = await _rpc(h, "CreateTransaction", {"id": "pm-b", "time": payments.now_ms(), "amount": amount,
                                                "account": account})
    assert other["error"]["code"] == -31050


async def test_payme_timeout(stand, monkeypatch):
    h, inv = stand, stand.invoice
    account, amount = {"order_id": str(inv.id)}, inv.amount * 100
    start = payments.now_ms()
    await _rpc(h, "CreateTransaction", {"id": "pm-t", "time": start, "amount": amount, "account": account})
    monkeypatch.setattr(payments, "now_ms", lambda: start + payments.PAYME_TIMEOUT_MS + 1000)   # прошло > 12 часов
    assert (await _rpc(h, "PerformTransaction", {"id": "pm-t"}))["error"]["code"] == -31008
    check = (await _rpc(h, "CheckTransaction", {"id": "pm-t"}))["result"]
    assert check["state"] == -1 and check["reason"] == 4                  # отменена по тайм-ауту
    assert (await _agency(h)).paid_until is None


async def test_payme_statement(stand):
    h, inv = stand, stand.invoice
    t0 = payments.now_ms()
    await _rpc(h, "CreateTransaction", {"id": "pm-s", "time": t0, "amount": inv.amount * 100,
                                        "account": {"order_id": str(inv.id)}})
    rows = (await _rpc(h, "GetStatement", {"from": t0 - 1000, "to": t0 + 1000}))["result"]["transactions"]
    assert [(r["id"], r["state"], r["amount"], r["account"]) for r in rows] == \
        [("pm-s", 1, inv.amount * 100, {"order_id": str(inv.id)})]
    assert (await _rpc(h, "GetStatement", {"from": t0 + 5000, "to": t0 + 9000}))["result"]["transactions"] == []


# ---------------------------------------------------------------------------
# Click
# ---------------------------------------------------------------------------
def _click_form(inv, action, amount=None, prepare_id=None, error="0", trans_id="9001"):
    form = {"click_trans_id": trans_id, "service_id": "111", "click_paydoc_id": "77",
            "merchant_trans_id": str(inv.id), "amount": amount or f"{inv.amount}.00", "action": str(action),
            "error": error, "error_note": "Success", "sign_time": "2026-10-18 12:00:00"}
    if prepare_id is not None:
        form["merchant_prepare_id"] = str(prepare_id)
    parts = [form["click_trans_id"], form["service_id"], CLICK_SECRET, form["merchant_trans_id"]]
    if action == 1:
        parts.append(form["merchant_prepare_id"])
    parts += [form["amount"], form["action"], form["sign_time"]]
    form["sign_string"] = hashlib.md5("".join(parts).encode()).hexdigest()     # как в документации Click
    return form


async def _click(h, form, action):
    async with h.maker() as session:
        return await payments.click_dispatch(session, form, action)


async def test_click_prepare_complete(stand):
    h, inv = stand, stand.invoice
    prep = await _click(h, _click_form(inv, 0), 0)
    assert prep["error"] == 0 and prep["merchant_prepare_id"] == inv.id
    done = await _click(h, _click_form(inv, 1, prepare_id=inv.id), 1)
    assert done["error"] == 0 and done["merchant_confirm_id"] == inv.id
    row = await _agency(h)
    assert row.plan == "pro" and row.paid_until > timeutils.now() + D(days=89)
    assert any("💰 Оплата (click)" in t for t in _texts(h.api, 1))
    again = await _click(h, _click_form(inv, 1, prepare_id=inv.id), 1)
    assert again["error"] == -4                                           # уже оплачено
    assert (await _agency(h)).paid_until == row.paid_until


async def test_click_errors(stand):
    h, inv = stand, stand.invoice
    forged = _click_form(inv, 0)
    forged["amount"] = "1.00"                                             # подпись больше не сходится
    assert (await _click(h, forged, 0))["error"] == -1
    assert (await _click(h, _click_form(inv, 0, amount="1000.00"), 0))["error"] == -2
    missing = _click_form(type("I", (), {"id": 999999, "amount": inv.amount})(), 0)
    assert (await _click(h, missing, 0))["error"] == -5
    assert (await _click(h, _click_form(inv, 0), 1))["error"] == -3      # действие не того адреса
    # complete без prepare
    assert (await _click(h, _click_form(inv, 1, prepare_id=inv.id), 1))["error"] == -6


async def test_click_failed_payment_cancels(stand):
    h, inv = stand, stand.invoice
    await _click(h, _click_form(inv, 0), 0)
    failed = await _click(h, _click_form(inv, 1, prepare_id=inv.id, error="-5017"), 1)   # Click: оплата не прошла
    assert failed["error"] == -9
    assert (await _agency(h)).paid_until is None
    async with h.maker() as session:
        assert (await session.get(Payment, inv.id)).state == -1


async def test_http_routes(stand):
    h, inv = stand, stand.invoice
    app = web.Application()
    payments.register_routes(app, lambda: h.maker)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/billing/click/prepare", data=_click_form(inv, 0))
        assert resp.status == 200 and (await resp.json())["error"] == 0
        resp = await client.post("/billing/click/complete", data=_click_form(inv, 1, prepare_id=inv.id))
        assert (await resp.json())["error"] == 0
        resp = await client.post("/billing/payme", headers={"Authorization": AUTH}, json={
            "id": 7, "method": "CheckTransaction", "params": {"id": "x"}})
        body = await resp.json()
        assert resp.status == 200 and body["id"] == 7 and body["error"]["code"] == -31003
    assert (await _agency(h)).plan == "pro"


# ---------------------------------------------------------------------------
# Ссылки
# ---------------------------------------------------------------------------
def test_links(env):
    inv = Payment(id=42, agency_id=2, plan="start", months=1, amount=420_000)
    payme = payments.payme_link(inv, "https://t.me/platform_bot")
    assert payme.startswith("https://checkout.paycom.uz/")
    assert base64.b64decode(payme.rsplit("/", 1)[1]).decode() == \
        "m=5e730e8e0b852a417aa49ceb;ac.order_id=42;a=42000000;c=https://t.me/platform_bot"
    click = payments.click_link(inv)
    assert click == "https://my.click.uz/services/pay?service_id=111&merchant_id=222&amount=420000.00&transaction_param=42"


def test_links_absent_without_credentials(monkeypatch):
    for name in ("PAYME_MERCHANT_ID", "PAYME_KEY", "CLICK_SERVICE_ID", "CLICK_MERCHANT_ID", "CLICK_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    inv = Payment(id=1, agency_id=2, plan="start", months=1, amount=1)
    assert payments.payme_link(inv) is None and payments.click_link(inv) is None


# ---------------------------------------------------------------------------
# /pay в боте платформы
# ---------------------------------------------------------------------------
@pytest.fixture
async def platform_stand(make_harness, monkeypatch):
    from tests.test_platform import _Platform

    h = await make_harness()
    async with h.maker() as session:
        session.add_all([Agency(id=2, name="Два", admin_ids=str(ADMIN2), owner_telegram_id=ADMIN2,
                                trial_until=timeutils.now() + D(days=5)),
                         Agency(id=3, name="Чужое", admin_ids="3003", owner_telegram_id=3003)])
        await session.commit()
    p = _Platform(h, monkeypatch)
    yield p
    p.detach()


async def test_pay_flow_gives_links(platform_stand, env):
    from tests.flow import buttons

    p = platform_stand
    await p.msg(ADMIN2, "/pay")                                     # одно агентство — сразу тарифы
    assert "Пробный период до" in p.texts(ADMIN2)[-1]
    assert ("Про — 590 000 сум", "pay:p:2:pro") in buttons(p.api.calls[-1])
    await p.cb(ADMIN2, "pay:p:2:pro")
    assert ("3 мес — 1 770 000 сум", "pay:m:2:pro:3") in buttons(p.api.calls[-1])
    await p.cb(ADMIN2, "pay:m:2:pro:3")
    last = p.api.calls[-1]
    assert "Итого: <b>1 770 000 сум</b>" in last.text
    urls = [b.url for row in last.reply_markup.inline_keyboard for b in row]
    async with p.h.maker() as session:
        invoice = (await session.scalars(select(Payment))).one()
    assert (invoice.agency_id, invoice.plan, invoice.months, invoice.amount, invoice.payer_id) == \
        (2, "pro", 3, 1_770_000, ADMIN2)
    payme = base64.b64decode(urls[0].rsplit("/", 1)[1]).decode()
    assert payme.startswith(f"m=5e730e8e0b852a417aa49ceb;ac.order_id={invoice.id};a=177000000")
    assert f"transaction_param={invoice.id}" in urls[1] and "amount=1770000.00" in urls[1]


async def test_pay_only_own_agency(platform_stand, env):
    p = platform_stand
    await p.cb(ADMIN2, "pay:m:3:pro:1")                             # чужое агентство — счёт не выставляется
    await p.cb(ADMIN2, "pay:m:2:pro:2")                             # срок не из списка — тоже
    async with p.h.maker() as session:
        assert (await session.scalars(select(Payment))).all() == []
    await p.msg(4004, "/pay")
    assert "Нет агентства для оплаты" in p.texts(4004)[-1]


async def test_pay_without_providers_notifies_platform(platform_stand, monkeypatch):
    for name in ("PAYME_MERCHANT_ID", "PAYME_KEY", "CLICK_SERVICE_ID", "CLICK_MERCHANT_ID", "CLICK_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    p = platform_stand
    await p.cb(ADMIN2, "pay:m:2:start:1")
    assert "Онлайн-оплата пока не подключена" in p.texts(ADMIN2)[-1]
    assert any("Счёт №" in t and "Два" in t for t in p.texts(1))


async def test_manual_payment_by_platform_owner(platform_stand):
    p = platform_stand
    await p.msg(ADMIN2, "/paid 2 3 pro")                            # не владелец платформы — не работает
    async with p.h.maker() as session:
        assert (await session.get(Agency, 2)).paid_until is None
    await p.msg(1, "/paid 2 3 pro")
    assert "«Про» · оплачено до" in p.texts(1)[-1]
    async with p.h.maker() as session:
        row = await session.get(Agency, 2)
        assert row.plan == "pro" and row.paid_until > timeutils.now() + D(days=89)
        manual = (await session.scalars(select(Payment))).one()
        assert (manual.provider, manual.state, manual.amount) == ("manual", 2, 1_770_000)
    await p.msg(1, "/paid 1 3")                                      # агентство №1 — без подписки
    await p.msg(1, "/paid 2 3 gold")
    assert all("Использование" in t for t in p.texts(1)[-2:])
