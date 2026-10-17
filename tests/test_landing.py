"""Лендинг на домене бота: страница и картинка превью."""
from __future__ import annotations

import io

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.config import get_settings
from bot.web.landing import register_landing


async def _client(monkeypatch, **settings) -> TestClient:
    cfg = get_settings()
    base = {"landing_demo_url": "https://t.me/demo_bot", "landing_contact_url": "https://t.me/seller",
            "landing_video_url": "", "webhook_url": "https://realtor.example"}
    for key, value in {**base, **settings}.items():
        monkeypatch.setattr(cfg, key, value)
    app = web.Application()
    register_landing(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_landing_page(monkeypatch):
    client = await _client(monkeypatch)
    try:
        resp = await client.get("/")
        html = await resp.text()
        assert resp.status == 200 and resp.content_type == "text/html"
        assert "{{" not in html                                      # все подстановки сделаны
        assert 'href="https://t.me/demo_bot"' in html and 'href="https://t.me/seller"' in html
        assert "Объявления, заявки и клиенты" in html and "bitta Telegram-botda" in html
        assert 'content="https://realtor.example/og.jpg"' in html
        assert "<iframe" not in html                                 # без видео — без блока
    finally:
        await client.close()


async def test_landing_video_and_fallback_links(monkeypatch):
    client = await _client(monkeypatch, landing_demo_url="", landing_video_url="https://www.youtube.com/embed/abc")
    try:
        html = await (await client.get("/")).text()
        assert 'src="https://www.youtube.com/embed/abc"' in html
        assert 'href="https://t.me/demo_bot"' not in html           # нет демо → ведём на контакт
        assert html.count('href="https://t.me/seller"') >= 5
    finally:
        await client.close()


async def test_og_image(monkeypatch):
    from PIL import Image

    client = await _client(monkeypatch)
    try:
        resp = await client.get("/og.jpg")
        assert resp.status == 200 and resp.content_type == "image/jpeg"
        assert Image.open(io.BytesIO(await resp.read())).size == (1200, 630)
    finally:
        await client.close()
