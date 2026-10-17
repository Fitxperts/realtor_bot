"""Каталог: просмотр активных объявлений прямо в боте (лентой) с фильтрами.

Решает проблему «пустого бота»: клиент может листать реальные объекты, а не
только заполнять анкету. У каждой карточки — кнопка «Оставить заявку», которая
заводит клиента в CRM (через общий поток lead). Фильтры: сделка, район (только
где есть объявления, с числом), комнаты, цена «до» — хранятся в данных кнопок
(bot/services/catalog_filter.py), без состояния.

Кнопки: cat:deal:<сделка> · cat:more:<сделка>:<сдвиг> (старые сообщения) ·
cat:f:<фильтр> — открыть панель · cat:fp:<фильтр> — обновить панель ·
cat:fd|fr|fx:<фильтр> — выбор района/комнат/цены · cat:s:<фильтр>:<сдвиг> — показать.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot import i18n
from bot.handlers.client import begin_client_form
from bot.handlers.lead import begin_lead
from bot.services import analytics
from bot.services.catalog_filter import (
    DISTRICTS,
    PRICE_BUCKETS,
    ROOMS,
    CatalogFilter,
    district_counts,
    filtered,
)
from bot.utils.formatters import format_property_card
from bot.utils.media import photo_input

logger = logging.getLogger(__name__)
router = Router(name="catalog")

PREFIX = "cat"
PAGE = 3  # объявлений за один показ
_CAPTION_LIMIT = 1024


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _deal_filter_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(i18n.btn("deal_rent", lang), f"{PREFIX}:deal:rent"),
         _btn(i18n.btn("deal_buy", lang), f"{PREFIX}:deal:sale"),
         _btn(i18n.btn("deal_all", lang), f"{PREFIX}:deal:all")],
        [_btn(i18n.btn("cat_filter", lang), f"{PREFIX}:f:{CatalogFilter().encode()}")],
    ])


def _card_kb(property_id: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(i18n.btn("lead_cta", lang), f"{PREFIX}:lead:{property_id}")]])


def _money(amount: int) -> str:
    return "$" + f"{amount:,}".replace(",", " ")


# ---------------------------------------------------------------------------
# Вход: кнопка меню «Объявления» или инлайн «Смотреть объявления»
# ---------------------------------------------------------------------------
@router.message(F.text.in_(i18n.btn_variants("catalog")))
async def catalog_menu(message: Message, lang: str) -> None:
    await message.answer(i18n.t("catalog_pick", lang), reply_markup=_deal_filter_kb(lang))


@router.callback_query(F.data.startswith(f"{PREFIX}:deal:"))
async def catalog_deal(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    await callback.answer()
    await _show_batch(callback.message, session, lang, CatalogFilter.decode(callback.data.split(":")[2]), 0)


@router.callback_query(F.data.startswith(f"{PREFIX}:more:"))
async def catalog_more(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    _, _, deal, offset = callback.data.split(":")
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)  # гасим старую «Показать ещё»
    await _show_batch(callback.message, session, lang, CatalogFilter.decode(deal), int(offset))


@router.callback_query(F.data.startswith(f"{PREFIX}:s:"))
async def catalog_show(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    raw = callback.data.split(":", 2)[2]
    encoded, _, offset = raw.rpartition(":")
    await callback.answer()
    if int(offset or 0):
        await callback.message.edit_reply_markup(reply_markup=None)
    await _show_batch(callback.message, session, lang, CatalogFilter.decode(encoded), int(offset or 0))


# ---------------------------------------------------------------------------
# Панель фильтра
# ---------------------------------------------------------------------------
async def _panel(session: AsyncSession, f: CatalogFilter, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    count = len(await filtered(session, f))
    deal = {"rent": i18n.btn("deal_rent", lang), "sale": i18n.btn("deal_buy", lang)}.get(
        f.deal, i18n.btn("deal_all", lang))
    rooms = i18n.t("f_any_rooms", lang) if f.rooms is None else ("4+" if f.rooms == 4 else str(f.rooms))
    price = (i18n.t("f_price_upto", lang, amount=_money(int(f.max_usd))) if f.max_usd
             else i18n.t("f_any_price", lang))
    text = i18n.t("f_panel", lang, deal=deal, count=count, rooms=rooms, price=price,
                  district=DISTRICTS[f.district] if f.district is not None else i18n.t("f_any_district", lang))
    enc = f.encode()
    # смена сделки сбрасывает цену: «до $300» у аренды и у продажи — разные шкалы
    deals = [_btn(("• " if f.deal == d else "") + i18n.btn(key, lang),
                  f"{PREFIX}:fp:{(f if d == f.deal else f.with_(deal=d, price=None)).encode()}")
             for d, key in (("rent", "deal_rent"), ("sale", "deal_buy"), ("all", "deal_all"))]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        deals,
        [_btn(i18n.btn("f_district", lang), f"{PREFIX}:fd:{enc}"),
         _btn(i18n.btn("f_rooms", lang), f"{PREFIX}:fr:{enc}"),
         _btn(i18n.btn("f_price", lang), f"{PREFIX}:fx:{enc}")],
        [_btn(i18n.btn("f_show", lang).format(n=count), f"{PREFIX}:s:{enc}:0"),
         _btn(i18n.btn("f_reset", lang), f"{PREFIX}:fp:{CatalogFilter(f.deal).encode()}")],
    ])
    return text, kb


@router.callback_query(F.data.startswith(f"{PREFIX}:f:"))
async def filter_open(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    await callback.answer()
    text, kb = await _panel(session, CatalogFilter.decode(callback.data.split(":", 2)[2]), lang)
    await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith(f"{PREFIX}:fp:"))
async def filter_update(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    await callback.answer()
    text, kb = await _panel(session, CatalogFilter.decode(callback.data.split(":", 2)[2]), lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001 - «message is not modified» и т.п. — не страшно
        logger.debug("Панель фильтра не изменилась")


def _options_kb(rows: list[list[InlineKeyboardButton]], f: CatalogFilter, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[*rows, [_btn("⬅️", f"{PREFIX}:fp:{f.encode()}")]])


@router.callback_query(F.data.startswith(f"{PREFIX}:fd:"))
async def filter_district(callback: CallbackQuery, session: AsyncSession, lang: str) -> None:
    f = CatalogFilter.decode(callback.data.split(":", 2)[2])
    counts = await district_counts(session, f)
    if not counts:
        await callback.answer(i18n.t("f_no_districts", lang), show_alert=True)
        return
    await callback.answer()
    buttons = [_btn(f"{name} ({n})", f"{PREFIX}:fp:{f.with_(district=i).encode()}") for i, name, n in counts]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([_btn(i18n.btn("f_any", lang), f"{PREFIX}:fp:{f.with_(district=None).encode()}")])
    await callback.message.edit_reply_markup(reply_markup=_options_kb(rows, f, lang))


@router.callback_query(F.data.startswith(f"{PREFIX}:fr:"))
async def filter_rooms(callback: CallbackQuery, lang: str) -> None:
    f = CatalogFilter.decode(callback.data.split(":", 2)[2])
    await callback.answer()
    rows = [[_btn("4+" if r == 4 else str(r), f"{PREFIX}:fp:{f.with_(rooms=r).encode()}") for r in ROOMS],
            [_btn(i18n.btn("f_any", lang), f"{PREFIX}:fp:{f.with_(rooms=None).encode()}")]]
    await callback.message.edit_reply_markup(reply_markup=_options_kb(rows, f, lang))


@router.callback_query(F.data.startswith(f"{PREFIX}:fx:"))
async def filter_price(callback: CallbackQuery, lang: str) -> None:
    f = CatalogFilter.decode(callback.data.split(":", 2)[2])
    if f.deal not in PRICE_BUCKETS:
        await callback.answer(i18n.t("f_price_need_deal", lang), show_alert=True)
        return
    await callback.answer()
    buttons = [_btn(i18n.t("f_price_upto", lang, amount=_money(v)), f"{PREFIX}:fp:{f.with_(price=i).encode()}")
               for i, v in enumerate(PRICE_BUCKETS[f.deal])]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([_btn(i18n.btn("f_any", lang), f"{PREFIX}:fp:{f.with_(price=None).encode()}")])
    await callback.message.edit_reply_markup(reply_markup=_options_kb(rows, f, lang))


# ---------------------------------------------------------------------------
# Заявка с карточки и пустая выдача
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith(f"{PREFIX}:lead:"))
async def catalog_lead(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot, lang: str
) -> None:
    property_id = callback.data.split(":", 2)[2]
    await callback.answer()
    await begin_lead(callback.message, state, session, lang, property_id, bot, user=callback.from_user)


@router.callback_query(F.data == f"{PREFIX}:req")
async def catalog_request(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await begin_client_form(callback.message, state, lang, full_name=callback.from_user.full_name)


async def _show_batch(message: Message, session: AsyncSession, lang: str, f: CatalogFilter, offset: int) -> None:
    items = await filtered(session, f)
    filter_btn = _btn(i18n.btn("cat_filter", lang), f"{PREFIX}:f:{f.encode()}")
    lead_btn = _btn(i18n.btn("lead_cta", lang), f"{PREFIX}:req")
    if not items:
        key = "catalog_empty" if f.is_empty and f.deal == "all" else "catalog_empty_filter"
        await message.answer(i18n.t(key, lang), reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[lead_btn]] if key == "catalog_empty" else [[filter_btn, lead_btn]]))
        return

    page = items[offset:offset + PAGE]
    for prop in page:
        caption = format_property_card(prop, lang)[:_CAPTION_LIMIT]
        kb = _card_kb(prop.id, lang)
        photos = prop.photo_list
        try:
            if photos:
                await message.answer_photo(photo_input(photos[0]), caption=caption, reply_markup=kb)
            else:
                await message.answer(caption, reply_markup=kb)
        except Exception:  # noqa: BLE001 - битое фото не должно рвать выдачу
            await message.answer(caption, reply_markup=kb)

    await analytics.track_many("catalog_view", [p.id for p in page], telegram_id=message.chat.id)
    shown = offset + len(page)
    if shown < len(items):
        await message.answer(
            i18n.t("catalog_footer", lang, shown=shown, total=len(items)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                _btn(i18n.btn("cat_more", lang), f"{PREFIX}:s:{f.encode()}:{shown}"), filter_btn,
            ]]),
        )
    else:
        await message.answer(i18n.t("catalog_end", lang),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[[lead_btn, filter_btn]]))
