"""WhatsApp: вебхук агентства (проверка, подпись), заявка админам, автоответ, настройка владельцем платформы."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from aiogram.methods import DeleteMessage
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from bot.database.models import Agency, Event
from bot.services import analytics, whatsapp
from bot.tenancy import agency_scope
from tests.flow import buttons

ADMIN2 = 2004
PHONE_ID = "106540352242922"
SECRET = "app-secret-xyz"


def _payload(msg_id="wamid.1", sender="998901234567", text="Salom, 2 xonali kvartira bormi?", phone_id=PHONE_ID):
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "998712000000", "phone_number_id": phone_id},
        "contacts": [{"profile": {"name": "Aziz"}, "wa_id": sender}],
        "messages": [{"from": sender, "id": msg_id, "timestamp": "1760000000", "type": "text",
                      "text": {"body": text}}]}}]}]}


def _sign(body: bytes, secret=SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_and_parse():
    body = json.dumps(_payload()).encode()
    assert whatsapp.signature_ok(body, _sign(body), SECRET)
    assert not whatsapp.signature_ok(body + b" ", _sign(body), SECRET)
    assert not whatsapp.signature_ok(body, None, SECRET) and not whatsapp.signature_ok(body, "abc", SECRET)
    [msg] = whatsapp.parse(_payload(), PHONE_ID)
    assert (msg.sender, msg.name, msg.text) == ("998901234567", "Aziz", "Salom, 2 xonali kvartira bormi?")
    assert whatsapp.parse(_payload(phone_id="999"), PHONE_ID) == []          # чужой номер — мимо
    image = _payload()
    image["entry"][0]["changes"][0]["value"]["messages"][0] = {"from": "998901234567", "id": "wamid.2",
                                                               "type": "image", "image": {"caption": "Uy rasmi"}}
    assert whatsapp.parse(image, PHONE_ID)[0].text == "[image] Uy rasmi"
    no_number = _payload()                                                    # пользователь без номера (username)
    no_number["entry"][0]["changes"][0]["value"]["contacts"] = [{"profile": {"name": "X"}, "user_id": "UZ.1"}]
    no_number["entry"][0]["changes"][0]["value"]["messages"][0]["from"] = "UZ.1"
    assert whatsapp.parse(no_number, PHONE_ID)[0].sender == ""


@pytest.fixture
async def stand(make_harness, monkeypatch):
    monkeypatch.setenv("PLATFORM_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(whatsapp, "_seen", type(whatsapp._seen)())
    monkeypatch.setattr(whatsapp, "_replied", {})
    sent = []

    async def fake_send(creds, to, text):
        sent.append((creds.phone_id, creds.token, to, text))
        return True

    monkeypatch.setattr(whatsapp, "send_text", fake_send)
    h = await make_harness()
    h.bot2, h.api2 = h.add_agency_bot(2, "43:AGENCY2", admin_ids=str(ADMIN2), bot_username="namangan_uy_bot",
                                      name="Namangan Uy")
    async with h.maker() as session:
        row = Agency(id=2, name="Namangan Uy", admin_ids=str(ADMIN2))
        h.verify = whatsapp.configure(row, PHONE_ID, "EAAG-token", SECRET)
        session.add(row)
        await session.commit()
    h.sent_wa = sent
    app = web.Application()
    whatsapp.register_routes(app, lambda: h.maker)
    h.client = TestClient(TestServer(app))
    await h.client.start_server()
    yield h
    await h.client.close()


async def _post(h, payload, agency="a2", sign=True):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = _sign(body)
    return await h.client.post(f"/whatsapp/{agency}", data=body, headers=headers)


async def test_verification_challenge(stand):
    h = stand
    ok = await h.client.get("/whatsapp/a2", params={"hub.mode": "subscribe", "hub.verify_token": h.verify,
                                                    "hub.challenge": "1158201444"})
    assert ok.status == 200 and await ok.text() == "1158201444"
    bad = await h.client.get("/whatsapp/a2", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                     "hub.challenge": "1"})
    assert bad.status == 403
    assert (await h.client.get("/whatsapp/a3", params={"hub.mode": "subscribe"})).status == 403   # не настроено


async def test_message_becomes_lead_and_reply_once(stand):
    h = stand
    assert (await _post(h, _payload(), sign=False)).status == 401              # без подписи — отказ
    assert not h.api2.calls

    assert (await _post(h, _payload())).status == 200
    notes = [c for c in h.api2.calls if getattr(c, "chat_id", None) == ADMIN2]
    assert len(notes) == 1 and "WhatsApp" in notes[0].text and "+998901234567" in notes[0].text
    assert "2 xonali kvartira" in notes[0].text
    assert buttons(notes[0]) == [("💬 Ответить в WhatsApp", None)]
    assert notes[0].reply_markup.inline_keyboard[0][0].url == "https://wa.me/998901234567"
    assert len(h.sent_wa) == 1 and h.sent_wa[0][:3] == (PHONE_ID, "EAAG-token", "998901234567")
    assert "Namangan Uy" in h.sent_wa[0][3] and "https://t.me/namangan_uy_bot" in h.sent_wa[0][3]

    await _post(h, _payload())                                                 # повтор доставки — без дубля
    assert len([c for c in h.api2.calls if getattr(c, "chat_id", None) == ADMIN2]) == 1
    await _post(h, _payload(msg_id="wamid.9", text="Narxi qancha?"))           # новое сообщение — админам да,
    assert len([c for c in h.api2.calls if getattr(c, "chat_id", None) == ADMIN2]) == 2
    assert len(h.sent_wa) == 1                                                 # автоответ — нет (раз в сутки)

    async with h.maker() as session:
        with agency_scope(2):
            assert await session.scalar(select(func.count()).select_from(Event).where(Event.type == "wa_lead")) == 2
        with agency_scope(2):
            report = await analytics.activity_report(session, "ru")
    assert "💬 Сообщения в WhatsApp: <b>2</b>" in report


async def test_disabled_agency_and_wrong_secret(stand):
    h = stand
    body = json.dumps(_payload()).encode()
    resp = await h.client.post("/whatsapp/a2", data=body, headers={"X-Hub-Signature-256": _sign(body, "other")})
    assert resp.status == 401
    async with h.maker() as session:
        row = await session.get(Agency, 2)
        row.status = "disabled"
        await session.commit()
    assert (await _post(h, _payload())).status == 404
    assert not h.api2.calls


async def test_report_without_whatsapp_has_no_line(make_harness):
    h = await make_harness()
    async with h.maker() as session:
        assert "WhatsApp" not in await analytics.activity_report(session, "ru")


async def test_superadmin_configures(make_harness, monkeypatch):
    from tests.test_platform import _Platform

    h = await make_harness()
    async with h.maker() as session:
        session.add(Agency(id=2, name="Namangan Uy"))
        await session.commit()
    p = _Platform(h, monkeypatch)
    from bot.config import get_settings
    monkeypatch.setattr(get_settings(), "webhook_url", "https://realtor.example.uz")
    try:
        await p.msg(950, f"/whatsapp 2 {PHONE_ID} EAAG-token {SECRET}")          # не владелец платформы
        async with h.maker() as session:
            assert (await session.get(Agency, 2)).wa_phone_id is None
        await p.msg(1, f"/whatsapp 2 {PHONE_ID} EAAG-token {SECRET}")
        assert any(isinstance(c, DeleteMessage) for c in p.api.calls)          # ключи удалены из чата
        reply = p.texts(1)[-1]
        assert "https://realtor.example.uz/whatsapp/a2</code>" in reply and "Verify token" in reply and "EAAG-token" not in reply
        async with h.maker() as session:
            row = await session.get(Agency, 2)
            creds = whatsapp.credentials(row)
        assert creds.token == "EAAG-token" and "EAAG" not in row.wa_token_enc and creds.verify_token in reply
        await p.msg(1, "/whatsapp 2")
        assert f"phone_number_id) {PHONE_ID}" in p.texts(1)[-1]
        await p.msg(1, "/whatsapp 2 off")
        async with h.maker() as session:
            assert whatsapp.credentials(await session.get(Agency, 2)) is None
    finally:
        p.detach()
