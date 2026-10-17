"""Демо-бот: сквозные сценарии посетителя + примеры, обложки и ночной сброс."""
from __future__ import annotations

import io

from aiogram.methods import DeleteMessage, SendPhoto
from aiogram.types import FSInputFile
from sqlalchemy import func, select

from bot.database import crud
from bot.database.models import Client, Property, PropertyStatus
from bot.handlers.demo import post_link
from bot.services import demo
from bot.services.catalog_filter import CatalogFilter, filtered
from tests.flow import buttons

ADMIN = 1          # ADMIN_TELEGRAM_ID из conftest
VISITOR = 700
LISTING = "Сдаётся 2 комнатная квартира Киргули 3/5 этаж мебель 300$ в месяц тел +998901234567"


async def _seed(h) -> None:
    async with h.maker() as session:
        await demo.ensure_seed(session)


async def _start_ru(h, uid=VISITOR) -> None:
    await h.message(uid, "/start")
    await h.callback(uid, "lang:ru")


async def _post_listing(h, uid=VISITOR, text=LISTING) -> None:
    await h.callback(uid, "role:owner")
    await h.message(uid, text)
    await h.callback(uid, "ow:pdone")
    await h.callback(uid, "ow:confirm")


# ---------------------------------------------------------------------------
# Сквозные сценарии (настоящий Dispatcher)
# ---------------------------------------------------------------------------
async def test_welcome_after_language(make_harness):
    h = await make_harness(demo_mode=True)
    await _start_ru(h)
    welcome = h.last_to(VISITOR)
    assert "Это демо РиелторБота" in welcome.text
    assert [data for _, data in buttons(welcome)] == ["cat:deal:all", "role:client", "role:owner", "demo:want"]

    # повторный /start: приветствие, без анкеты и без повторного выбора языка
    h.clear()
    await h.message(VISITOR, "/start")
    assert len(h.sent(VISITOR)) == 1 and "Это демо" in h.texts_to(VISITOR)[0]


async def test_want_bot_lead_reaches_admin(make_harness):
    h = await make_harness(demo_mode=True)
    await _start_ru(h)
    await h.callback(VISITOR, "demo:want")
    ask = h.last_to(VISITOR)
    assert "Оставьте номер" in ask.text and ask.reply_markup.keyboard[0][0].request_contact
    assert "хочет такой бот" in h.texts_to(ADMIN)[-1]

    await h.message(VISITOR, contact="+998901112233")
    assert "+998901112233" in h.texts_to(ADMIN)[-1] and "@user700" in h.texts_to(ADMIN)[-1]
    thanks = h.last_to(VISITOR)
    assert "Спасибо" in thanks.text
    assert ("💼 Хочу такой бот", None) in buttons(thanks)  # меню вернулось, кнопка демо в нём


async def test_want_bot_from_menu_button_and_bad_phone(make_harness):
    h = await make_harness(demo_mode=True)
    await _start_ru(h)
    await h.message(VISITOR, "💼 Хочу такой бот")
    await h.message(VISITOR, "не скажу")
    assert "номер" in h.last_to(VISITOR).text.lower()  # просим корректный телефон
    await h.message(VISITOR, "+998 90 111 22 33")
    assert "Спасибо" in h.last_to(VISITOR).text


async def test_catalog_shows_seed_with_cover_and_lead_view(make_harness):
    h = await make_harness(demo_mode=True)
    await _seed(h)
    await _start_ru(h)
    h.clear()
    await h.callback(VISITOR, "cat:deal:all")
    photos = [m for m in h.sent(VISITOR) if isinstance(m, SendPhoto)]
    assert len(photos) == 3 and all(isinstance(m.photo, FSInputFile) for m in photos)
    assert "из 18" in h.texts_to(VISITOR)[-1]

    await h.callback(VISITOR, "cat:lead:APT_1001")
    await h.message(VISITOR, contact="+998905556677")
    texts = h.texts_to(VISITOR)
    assert any("Спасибо! Заявка принята" in t for t in texts)
    view = next(t for t in texts if "Так вашу заявку видит агентство" in t)
    assert "APT_1001" in view and "+998905556677" in view
    assert texts[-1] == "Прислать похожие объекты, когда появятся?"  # подписка — только по согласию
    assert any("Заявка по объекту" in t for t in h.texts_to(ADMIN))


async def test_visitor_listing_published_to_channel(make_harness):
    h = await make_harness(demo_mode=True)
    await _seed(h)
    await _start_ru(h)
    h.clear()
    await _post_listing(h)

    texts = h.texts_to(VISITOR)
    assert any("опубликовано в демо-канале" in t and "https://t.me/demo_channel/" in t for t in texts)
    preview = h.last_to(VISITOR)
    assert "Объект на модерацию" in preview.text
    assert {data for _, data in buttons(preview)} == {"demo:noop"}
    post = h.texts_to("@demo_channel")[-1]
    assert "300 $" in post and "сўм" not in post          # валюта из «300$» не потерялась
    assert any("разместил объявление" in t for t in h.texts_to(ADMIN))
    assert not any("на модерацию" in t for t in h.texts_to(ADMIN))  # демо: без модерации

    async with h.maker() as session:
        prop = (await crud.list_owner_properties(session, VISITOR))[0]
        assert prop.status == PropertyStatus.active and prop.channel_post_id


async def test_noop_buttons_explain(make_harness):
    h = await make_harness(demo_mode=True)
    await h.callback(VISITOR, "demo:noop")
    answer = h.api.calls[-1]
    assert answer.show_alert and "для показа" in answer.text


async def test_daily_limit(make_harness):
    h = await make_harness(demo_mode=True, demo_daily_limit=2)
    await _start_ru(h)
    await _post_listing(h, text="Сдаётся 1 комнатная квартира Фрунзе 2/4 этаж 200$ тел +998901110001")
    await _post_listing(h, text="Продаю 3 комнатная квартира Текстиль 4/5 этаж 41000$ тел +998901110002")
    h.clear()
    await _post_listing(h, text="Сдаётся дом Маталка 5 комнат 600$ тел +998901110003")
    assert "до 2 объявлений в день" in h.texts_to(VISITOR)[-1]  # до проверки дублей и сохранения
    async with h.maker() as session:
        assert len(await crud.list_owner_properties(session, VISITOR)) == 2


async def test_regular_bot_unchanged(make_harness):
    """Без DEMO_MODE: обычный выбор роли, модерация, никаких демо-кнопок."""
    h = await make_harness(demo_mode=False)
    await _start_ru(h)
    assert "Это демо" not in "".join(h.texts_to(VISITOR))
    await _post_listing(h)
    assert any("на модерацию" in t for t in h.texts_to(VISITOR))
    assert any("Объект на модерацию" in t for t in h.texts_to(ADMIN))
    assert h.texts_to("@demo_channel") == []
    assert all("💼" not in text for m in h.sent(VISITOR) for text, _ in buttons(m))


# ---------------------------------------------------------------------------
# Примеры, обложки, сброс
# ---------------------------------------------------------------------------
async def test_seed_is_idempotent_and_restores(make_harness):
    h = await make_harness(demo_mode=True)
    async with h.maker() as session:
        assert await demo.ensure_seed(session) == 18
        prop = await session.get(Property, "HSE_1001")
        prop.status, prop.price = PropertyStatus.sold, 1
        await session.commit()
        assert await demo.ensure_seed(session) == 0
        await session.refresh(prop)
        assert prop.status == PropertyStatus.active and prop.price == 68_000
        assert len(await filtered(session, CatalogFilter())) == 18


async def test_reset_removes_visitor_data_keeps_seed(make_harness):
    h = await make_harness(demo_mode=True)
    await _seed(h)
    await _start_ru(h)
    await _post_listing(h)
    await h.callback(VISITOR, "cat:lead:APT_1002")
    await h.message(VISITOR, contact="+998905556677")
    async with h.maker() as session:
        visitor_prop = (await crud.list_owner_properties(session, VISITOR))[0]
        assert await session.scalar(select(func.count()).select_from(Client)) == 1

    h.clear()
    result = await demo.reset_demo(h.bot)
    assert result["properties"] == 1 and result["clients"] == 1
    deleted = [c for c in h.api.calls if isinstance(c, DeleteMessage)]
    assert [c.message_id for c in deleted] == [visitor_prop.channel_post_id]
    async with h.maker() as session:
        assert await session.get(Property, visitor_prop.id) is None
        assert await session.scalar(select(func.count()).select_from(Client)) == 0
        assert len(await filtered(session, CatalogFilter())) == 18


async def test_publish_missing_posts_seed_once(make_harness):
    h = await make_harness(demo_mode=True)
    await _seed(h)
    assert await demo.publish_missing(h.bot, pause=0) == 18
    assert len(h.texts_to("@demo_channel")) >= 18
    assert await demo.publish_missing(h.bot, pause=0) == 0  # повторно не публикуем


async def test_real_photos_used_when_present(make_harness, tmp_path):
    photos = tmp_path / "real"
    photos.mkdir()
    for name in ("apartment_1.jpg", "apartment_2.jpg", "house_1.jpg", "notes.txt"):
        (photos / name).write_bytes(b"x")
    await make_harness(demo_mode=True, demo_photos_dir=str(photos))
    pid, kind, deal, fields = demo.SEED[0]
    assert [p.rsplit("/", 1)[-1] for p in demo.seed_photos(0, pid, kind, deal, fields)] == [
        "apartment_1.jpg", "apartment_2.jpg"]
    pid, kind, deal, fields = demo.SEED[14]  # участок — своих фото нет → обложка
    assert demo.seed_photos(14, pid, kind, deal, fields)[0].endswith(f"demo/{pid}.jpg")


def test_covers_render_for_every_seed():
    from PIL import Image

    for pid, kind, deal, fields in demo.SEED:
        img = Image.open(io.BytesIO(demo.render_cover(pid, kind, deal, fields)))
        assert img.size == (1200, 900) and img.format == "JPEG"


def test_post_link():
    assert post_link("@demo_ch", 15) == "https://t.me/demo_ch/15"
    assert post_link("-1001234567890", 7) == "https://t.me/c/1234567890/7"
    assert post_link("", 7) is None and post_link("@x", None) is None
