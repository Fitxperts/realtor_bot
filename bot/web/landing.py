"""Лендинг на домене бота (режим webhook): https://<домен>/

Одна статичная страница (узбекский/русский) + картинка для превью ссылки в
Telegram (/og.jpg — обложка из демо). Ссылки на демо-бота и контакт продавца —
из .env: LANDING_DEMO_URL, LANDING_CONTACT_URL, по желанию LANDING_VIDEO_URL.
"""
from __future__ import annotations

from functools import lru_cache
from html import escape
from pathlib import Path

from aiohttp import web

from bot.config import CHANNEL_URL, get_settings

_TEMPLATE = Path(__file__).with_name("landing.html")

_VIDEO = """
  <section>
    <h2><span class="uz">60 soniyada koʻrib chiqing</span><span class="ru">Посмотрите за 60 секунд</span></h2>
    <div class="video"><iframe src="{url}" title="RieltorBot" allowfullscreen loading="lazy"></iframe></div>
  </section>
"""


def render_landing() -> str:
    settings = get_settings()
    contact = settings.landing_contact_url or settings.landing_demo_url or CHANNEL_URL
    demo = settings.landing_demo_url or contact
    base = settings.webhook_url.rstrip("/")
    video = _VIDEO.format(url=escape(settings.landing_video_url, quote=True)) if settings.landing_video_url else ""
    return (
        _TEMPLATE.read_text(encoding="utf-8")
        .replace("{{DEMO_URL}}", escape(demo, quote=True))
        .replace("{{CONTACT_URL}}", escape(contact, quote=True))
        .replace("{{OG_IMAGE}}", escape(f"{base}/og.jpg", quote=True))
        .replace("{{VIDEO_BLOCK}}", video)
    )


@lru_cache(maxsize=1)
def _og_image() -> bytes:
    """Превью ссылки 1200×630: фирменный градиент, название и суть продукта."""
    import io

    from PIL import Image, ImageDraw

    from bot.services.demo import _FONT, _FONT_BOLD, _font

    w, h = 1200, 630
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for x in range(w):  # градиент Telegram-синего
        k = x / w
        draw.line([(x, 0), (x, h)], fill=(int(29 + 13 * k), int(143 + 28 * k), int(204 + 34 * k)))
    draw.rounded_rectangle([80, 90, 180, 190], radius=24, fill=(255, 255, 255))
    draw.polygon([(105, 150), (130, 120), (155, 150)], fill=(42, 171, 238))
    draw.rectangle([112, 150, 148, 172], fill=(42, 171, 238))
    draw.text((210, 140), "RieltorBot", font=_font(_FONT_BOLD, 64), fill=(255, 255, 255), anchor="lm")
    draw.text((80, 280), "Объявления, заявки и клиенты —", font=_font(_FONT_BOLD, 52), fill=(255, 255, 255))
    draw.text((80, 350), "в одном Telegram-боте", font=_font(_FONT_BOLD, 52), fill=(255, 255, 255))
    draw.text((80, 470), "Eʼlonlar, arizalar va mijozlar — bitta botda", font=_font(_FONT, 36),
              fill=(225, 243, 255))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


async def landing(_request: web.Request) -> web.Response:
    return web.Response(text=render_landing(), content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "public, max-age=300"})


async def og_image(_request: web.Request) -> web.Response:
    return web.Response(body=_og_image(), content_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


def register_landing(app: web.Application) -> None:
    app.router.add_get("/", landing)
    app.router.add_get("/og.jpg", og_image)
