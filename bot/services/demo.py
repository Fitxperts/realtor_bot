"""Демо-режим (DEMO_MODE=true): живая демонстрация бота для продаж.

Отдельный бот со своей базой. В нём:
- 18 примеров объявлений по Фергане (аренда/продажа, квартиры, дома, участки,
  коммерция) с постоянными ID — посты в демо-канале не «ломаются» при сбросе;
- фото: реальные из DEMO_PHOTOS_DIR (apartment_*.jpg, house_*.jpg, land_*.jpg,
  commercial_*.jpg), иначе аккуратные обложки, нарисованные Pillow;
- каждую ночь (05:00) всё, что накликали посетители, удаляется (заявки,
  встречи, их объявления и посты в канале), примеры восстанавливаются.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.agency_config import cfg
from bot.config import get_settings
from bot.database.models import (
    Client,
    Meeting,
    Property,
    PropertyKind,
    PropertyStatus,
    PropertyType,
    SearchAlert,
)
from bot.database.session import get_sessionmaker

logger = logging.getLogger(__name__)

_A, _H, _L, _C = PropertyKind.apartment, PropertyKind.house, PropertyKind.land, PropertyKind.commercial
_RENT, _SALE = PropertyType.rent, PropertyType.sale
_ALL_COMMS = {"gas": True, "water": True, "electricity": True}

# (id, вид, сделка, поля). Цены — правдоподобные для Ферганы осенью 2026.
SEED: list[tuple[str, PropertyKind, PropertyType, dict]] = [
    ("APT_1001", _A, _RENT, dict(
        district="Киргули", address="возле Киргулийского базара", rooms=2, area=58, floor=3, floors=5,
        price=300, currency="$", furniture=True, appliances=True, internet=True, **_ALL_COMMS,
        renovation="Евроремонт",
        description="Светлая двухкомнатная квартира с евроремонтом. Полностью меблирована: кондиционер, "
                    "стиральная машина, холодильник. Рядом базар, школа и остановка.")),
    ("APT_1002", _A, _RENT, dict(
        district="Ахунбабаев", address="центр, рядом с парком Аль-Фергани", rooms=1, area=38, floor=2, floors=4,
        price=2_800_000, currency="сум", furniture=True, appliances=True, **_ALL_COMMS,
        description="Уютная однокомнатная квартира в центре города. Подойдёт студентам или молодой паре, "
                    "до парка и кафе — пять минут пешком.")),
    ("APT_1003", _A, _RENT, dict(
        district="ЭкоСити Аэропорт", address="ЖК в новом квартале, охраняемая территория", rooms=3, area=86,
        floor=7, floors=9, price=450, currency="$", furniture=True, appliances=True, internet=True,
        **_ALL_COMMS, renovation="Новостройка, дизайнерский ремонт",
        description="Просторная трёхкомнатная квартира в новостройке: лифт, детская площадка, парковка. "
                    "Две спальни и большая гостиная-кухня.")),
    ("APT_1004", _A, _RENT, dict(
        district="Фрунзе", address="напротив школы №21", rooms=2, area=52, floor=1, floors=4,
        price=3_500_000, currency="сум", furniture=True, **_ALL_COMMS,
        description="Двухкомнатная квартира на первом этаже, удобно для семьи с детьми. "
                    "Есть мебель, рядом школа, садик и магазины.")),
    ("APT_1005", _A, _SALE, dict(
        district="ЭкоСити Аэропорт", address="новостройка, сдача дома — 2026 год", rooms=2, area=64,
        floor=5, floors=9, price=42_000, currency="$", docs=True, mortgage=True, **_ALL_COMMS,
        renovation="Под чистовую отделку",
        description="Двухкомнатная квартира в новостройке, документы готовы. "
                    "Возможна ипотека. Тихий двор, рядом строится школа.")),
    ("APT_1006", _A, _SALE, dict(
        district="Текстиль", address="возле остановки «Текстиль»", rooms=3, area=72, floor=4, floors=5,
        price=38_000, currency="$", negotiable=True, docs=True, **_ALL_COMMS,
        renovation="Требуется косметический ремонт",
        description="Трёхкомнатная квартира, раздельные комнаты, два балкона. "
                    "Нужен косметический ремонт — цена с учётом этого, торг уместен.")),
    ("APT_1007", _A, _SALE, dict(
        district="Миндонобод", address="рядом с поликлиникой", rooms=1, area=34, floor=2, floors=5,
        price=21_500, currency="$", docs=True, **_ALL_COMMS,
        description="Однокомнатная квартира — хороший вариант под сдачу в аренду или для первого жилья. "
                    "Окна во двор, тихо.")),
    ("APT_1008", _A, _SALE, dict(
        district="Калининский", address="у Калининского рынка", rooms=4, area=98, floor=3, floors=4,
        price=55_000, currency="$", docs=True, furniture=True, **_ALL_COMMS, renovation="Хороший ремонт",
        description="Большая четырёхкомнатная квартира для большой семьи. Две лоджии, кладовая, "
                    "остаётся встроенная мебель.")),
    ("APT_1009", _A, _SALE, dict(
        district="Фрунзе", address="рядом с супермаркетом", rooms=2, area=54, floor=3, floors=4,
        price=33_500, currency="$", docs=True, **_ALL_COMMS,
        description="Двухкомнатная квартира в кирпичном доме, комнаты раздельные. "
                    "Свежие окна и двери, счётчики установлены.")),
    ("APT_1010", _A, _RENT, dict(
        district="Калининский", address="возле колледжа", rooms=2, area=50, floor=2, floors=5,
        price=3_800_000, currency="сум", furniture=True, appliances=True, internet=True, **_ALL_COMMS,
        description="Квартира с мебелью и техникой, интернет подключён. "
                    "Можно заселиться сразу, оплата помесячно.")),
    ("HSE_1001", _H, _SALE, dict(
        district="Киргули", address="тихая махалля, 6 соток", rooms=4, area=160,
        price=68_000, currency="$", docs=True, **_ALL_COMMS, renovation="Жилое состояние",
        description="Дом на шести сотках: четыре комнаты, летняя кухня, гараж и фруктовый сад. "
                    "Все коммуникации подключены, документы в порядке.")),
    ("HSE_1002", _H, _SALE, dict(
        district="Маталка", address="двухэтажный дом, 8 соток", rooms=5, area=220,
        price=95_000, currency="$", negotiable=True, docs=True, **_ALL_COMMS, renovation="Евроремонт",
        description="Двухэтажный дом с евроремонтом: пять комнат, два санузла, крытый двор и навес для "
                    "машины. Торг при осмотре.")),
    ("HSE_1003", _H, _RENT, dict(
        district="Военный городок", address="отдельный двор", rooms=3, area=110,
        price=400, currency="$", furniture=True, **_ALL_COMMS,
        description="Дом в аренду с отдельным двором — для семьи, можно с детьми. "
                    "Есть мебель и место для машины.")),
    ("HSE_1004", _H, _SALE, dict(
        district="Миндонобод", address="рядом со школой и мечетью", rooms=3, area=110,
        price=47_000, currency="$", negotiable=True, docs=True, **_ALL_COMMS,
        description="Крепкий одноэтажный дом на четырёх сотках. Три комнаты, большая кухня, "
                    "во дворе виноградник.")),
    ("LND_1001", _L, _SALE, dict(
        district="Маталка", address="6 соток, свет по границе участка", area=600,
        price=18_000, currency="$", docs=True, electricity=True,
        description="Ровный участок шесть соток под строительство дома. Электричество по границе, "
                    "газ — в 50 метрах, документы готовы.")),
    ("LND_1002", _L, _SALE, dict(
        district="ЭкоСити Аэропорт", address="10 соток у новой дороги", area=1000,
        price=32_000, currency="$", docs=True, electricity=True, water=True,
        description="Участок десять соток рядом с новым жилым кварталом. "
                    "Подходит под дом или небольшой коммерческий объект.")),
    ("COM_1001", _C, _RENT, dict(
        district="Ахунбабаев", address="первая линия, центр", area=45, floor=1, floors=2,
        price=550, currency="$", **_ALL_COMMS, renovation="Готово под магазин",
        description="Помещение 45 м² на первой линии с большой витриной. "
                    "Высокий поток людей — под магазин, аптеку или кофейню.")),
    ("COM_1002", _C, _SALE, dict(
        district="Фрунзе", address="отдельный вход, парковка", area=120, floor=1, floors=3,
        price=85_000, currency="$", docs=True, internet=True, **_ALL_COMMS, renovation="Офисный ремонт",
        description="Офис 120 м² с отдельным входом и парковкой. Пять кабинетов, санузел, "
                    "интернет и охранная сигнализация.")),
]
SEED_IDS = frozenset(pid for pid, *_ in SEED)
PUBLISH_PAUSE = 3.0  # сек между постами: лимит Telegram ~20 постов в минуту в канал

_KIND_TITLE = {_A: "KVARTIRA", _H: "HOVLI", _L: "YER UCHASTKASI", _C: "TIJORAT OBYEKTI"}
_KIND_RU = {_A: "квартира", _H: "дом", _L: "участок", _C: "коммерция"}
_PALETTE = {  # верх неба, низ неба, силуэт, акцент
    _A: ((29, 78, 137), (98, 152, 201), (18, 46, 82), (29, 78, 137)),
    _H: ((150, 70, 30), (232, 150, 90), (90, 40, 18), (170, 80, 30)),
    _L: ((46, 107, 60), (150, 200, 140), (30, 70, 38), (46, 107, 60)),
    _C: ((62, 44, 110), (130, 112, 196), (36, 26, 70), (78, 56, 140)),
}
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _price_label(price: float, currency: str, deal: PropertyType) -> str:
    amount = f"{int(price):,}".replace(",", " ")
    text = f"${amount}" if currency == "$" else f"{amount} so‘m"
    return text + (" / oy" if deal == _RENT else "")


def _font(path: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _fit(draw, text: str, path: str, size: int, max_width: int):
    font = _font(path, size)
    while size > 18 and draw.textlength(text, font=font) > max_width:
        size -= 2
        font = _font(path, size)
    return font


def render_cover(pid: str, kind: PropertyKind, deal: PropertyType, fields: dict) -> bytes:
    """Обложка объявления 1200×900: «картинка» по виду объекта + название, район, цена."""
    import io

    from PIL import Image, ImageDraw

    w, h, sky_h = 1200, 900, 560
    top, bottom, shape, accent = _PALETTE[kind]
    img = Image.new("RGB", (w, h), (250, 250, 248))
    draw = ImageDraw.Draw(img)
    for y in range(sky_h):  # градиент «неба»
        k = y / sky_h
        draw.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * k) for i in range(3)))

    win = tuple(min(255, c + 150) for c in shape)
    if kind == _A:  # три дома с окнами
        for x0, bw, bh in ((140, 260, 380), (450, 300, 470), (800, 250, 330)):
            draw.rectangle([x0, sky_h - bh, x0 + bw, sky_h], fill=shape)
            for wy in range(sky_h - bh + 30, sky_h - 40, 62):
                for wx in range(x0 + 28, x0 + bw - 40, 58):
                    draw.rectangle([wx, wy, wx + 30, wy + 36], fill=win)
    elif kind == _H:  # дом с крышей, дверью и окнами, дерево
        draw.rectangle([380, 300, 820, sky_h], fill=shape)
        draw.polygon([(340, 310), (600, 150), (860, 310)], fill=tuple(max(0, c - 25) for c in shape))
        draw.rectangle([560, 420, 640, sky_h], fill=win)
        for wx in (430, 690):
            draw.rectangle([wx, 360, wx + 80, 430], fill=win)
        draw.ellipse([920, 300, 1080, 460], fill=(40, 90, 40))
        draw.rectangle([990, 450, 1010, sky_h], fill=(70, 45, 25))
    elif kind == _L:  # холмы и межевые столбики
        draw.ellipse([-200, 380, 700, 900], fill=shape)
        draw.ellipse([400, 420, 1400, 940], fill=tuple(max(0, c - 15) for c in shape))
        for x in range(160, 1100, 120):
            draw.rectangle([x, 470, x + 10, 540], fill=(240, 230, 210))
        draw.line([(160, 490), (1110, 490)], fill=(240, 230, 210), width=4)
    else:  # витрина с полосатым навесом
        draw.rectangle([250, 230, 950, sky_h], fill=shape)
        for i, x in enumerate(range(230, 970, 74)):
            draw.polygon([(x, 230), (x + 74, 230), (x + 74, 300), (x, 300)],
                         fill=(240, 240, 240) if i % 2 else accent)
        draw.rectangle([300, 340, 640, sky_h - 30], fill=win)
        draw.rectangle([700, 340, 900, sky_h], fill=win)

    draw.rectangle([0, sky_h, w, h], fill=(250, 250, 248))  # панель текста поверх «вылезших» фигур

    # бейдж DEMO
    draw.rounded_rectangle([36, 36, 236, 100], radius=18, fill=(255, 255, 255))
    draw.text((136, 68), "DEMO", font=_font(_FONT_BOLD, 34), fill=accent, anchor="mm")

    rooms = fields.get("rooms")
    deal_uz = "IJARA" if deal == _RENT else "SOTUV"
    title = f"{rooms} XONALI {_KIND_TITLE[kind]}" if rooms and kind != _L else _KIND_TITLE[kind]
    title = f"{title} · {deal_uz}"
    area = fields.get("area")
    sub_ru = f"{rooms}-комн. {_KIND_RU[kind]}" if rooms and kind != _L else _KIND_RU[kind].capitalize()
    subtitle = " · ".join(x for x in (sub_ru, f"{area:g} м²" if area else "", fields.get("district")) if x)

    x0, max_w = 60, w - 120
    draw.text((x0, 600), title, font=_fit(draw, title, _FONT_BOLD, 56, max_w), fill=(25, 25, 30))
    draw.text((x0, 682), subtitle, font=_fit(draw, subtitle, _FONT, 36, max_w), fill=(95, 95, 105))
    price = _price_label(fields["price"], fields.get("currency", "$"), deal)
    draw.text((x0, 760), price, font=_fit(draw, price, _FONT_BOLD, 72, max_w), fill=accent)
    draw.text((w - 60, 860), pid, font=_font(_FONT, 24), fill=(170, 170, 175), anchor="rs")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _real_photos(kind: PropertyKind) -> list[Path]:
    folder = Path(get_settings().demo_photos_dir)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.name.lower().startswith(kind.value) and p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def seed_photos(index: int, pid: str, kind: PropertyKind, deal: PropertyType, fields: dict) -> list[str]:
    """Реальные фото этого вида (по кругу, до 3 шт.), иначе сгенерированная обложка."""
    pool = _real_photos(kind)
    if pool:
        start = (index * 3) % len(pool)
        return [str(pool[(start + i) % len(pool)]) for i in range(min(3, len(pool)))]
    path = Path(get_settings().media_dir) / "demo" / f"{pid}.jpg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render_cover(pid, kind, deal, fields))
    return [str(path)]


async def ensure_seed(session: AsyncSession) -> int:
    """Создать недостающие примеры и вернуть изменённые к исходному виду. Возвращает число созданных."""
    created = 0
    for index, (pid, kind, deal, fields) in enumerate(SEED):
        values = {
            "type": deal, "property_kind": kind, "status": PropertyStatus.active,
            "owner_name": "Демо-собственник", "owner_phone": f"+99890000{index + 1:04d}",
            "photos": ",".join(seed_photos(index, pid, kind, deal, fields)),
            "furniture": False, "appliances": False, "gas": False, "water": False, "electricity": False,
            "internet": False, "docs": False, "mortgage": False, "negotiable": False,
            "address": None, "rooms": None, "area": None, "floor": None, "floors": None, "renovation": None,
            **fields,
        }
        prop = await session.get(Property, pid)
        if prop is None:
            session.add(Property(id=pid, **values))
            created += 1
        else:
            for key, value in values.items():
                setattr(prop, key, value)
    await session.commit()
    return created


async def reset_demo(bot: Bot) -> dict:
    """Ночной сброс: удалить всё, что создали посетители, и восстановить примеры."""
    channel = cfg().channel_id
    async with get_sessionmaker()() as session:
        visitor_props = list(await session.scalars(select(Property).where(Property.id.not_in(SEED_IDS))))
        for prop in visitor_props:
            if prop.channel_post_id and channel:
                try:
                    await bot.delete_message(channel, prop.channel_post_id)
                except Exception:  # noqa: BLE001 - пост уже удалён / старше 48 ч
                    logger.debug("Демо: пост %s не удалён", prop.channel_post_id)
        await session.execute(delete(SearchAlert))
        meetings = (await session.execute(delete(Meeting))).rowcount
        clients = (await session.execute(delete(Client))).rowcount
        await session.execute(delete(Property).where(Property.id.not_in(SEED_IDS)))
        await session.commit()
        created = await ensure_seed(session)
    await publish_missing(bot)
    result = {"properties": len(visitor_props), "clients": clients, "meetings": meetings, "seeded": created}
    logger.info("Демо-сброс: %s", result)
    return result


async def publish_missing(bot: Bot, *, pause: float | None = None) -> int:
    """Опубликовать в демо-канал примеры, у которых ещё нет поста (первый запуск)."""
    from bot.services import publisher

    if not cfg().channel_id:
        return 0
    published = 0
    async with get_sessionmaker()() as session:
        ids = list(await session.scalars(
            select(Property.id).where(Property.id.in_(SEED_IDS), Property.channel_post_id.is_(None))
            .order_by(Property.id)
        ))
        for pid in ids:
            try:
                await publisher.publish_property(bot, session, pid)
                published += 1
            except Exception:  # noqa: BLE001 - канал недоступен: попробуем при следующем сбросе
                logger.exception("Демо: не удалось опубликовать %s", pid)
                break
            await asyncio.sleep(PUBLISH_PAUSE if pause is None else pause)
    return published


async def bootstrap(bot: Bot) -> None:
    """Старт демо-бота: примеры в базе и в канале."""
    try:
        async with get_sessionmaker()() as session:
            created = await ensure_seed(session)
        published = await publish_missing(bot)
        logger.info("Демо-режим: примеров создано %d, опубликовано в канал %d", created, published)
    except Exception:  # noqa: BLE001 - демо не должно ронять бота
        logger.exception("Демо: ошибка подготовки примеров")
