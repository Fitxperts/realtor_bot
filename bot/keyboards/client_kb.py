"""Инлайн-клавиатуры сценария клиента (мультиязычные)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import i18n
from bot.agency_config import cfg

PREFIX = "cl"

# Слоты по времени суток
TIME_SLOTS = {
    "morning": ["09:00", "10:00", "11:00"],
    "day": ["12:00", "13:00", "14:00", "15:00"],
    "evening": ["16:00", "17:00", "18:00"],
}
PERIODS = {"morning": "period_morning", "day": "period_day", "evening": "period_evening"}


def deal_type_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n.btn("deal_rent", lang), callback_data=f"{PREFIX}:deal:rent"),
                InlineKeyboardButton(text=i18n.btn("deal_buy", lang), callback_data=f"{PREFIX}:deal:buy"),
            ]
        ]
    )


def _districts() -> list[str]:
    """Районы города текущего агентства (справочник — bot/services/geo.py)."""
    return [name for name, _ in cfg().districts]


def districts_kb(lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    names = _districts()
    for i in range(0, len(names), 2):
        row = [
            InlineKeyboardButton(text=name, callback_data=f"{PREFIX}:dist:{idx}")
            for idx, name in enumerate(names[i : i + 2], start=i)
        ]
        rows.append(row)
    rows.append(
        [InlineKeyboardButton(text=i18n.btn("district_other", lang), callback_data=f"{PREFIX}:dist:other")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n.btn("confirm_send", lang), callback_data=f"{PREFIX}:confirm"),
                InlineKeyboardButton(text=i18n.btn("cancel", lang), callback_data=f"{PREFIX}:cancel"),
            ]
        ]
    )


def period_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n.t(msg_key, lang), callback_data=f"{PREFIX}:period:{key}")
                for key, msg_key in PERIODS.items()
            ]
        ]
    )


def slots_kb(period: str) -> InlineKeyboardMarkup:
    slots = TIME_SLOTS.get(period, [])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=slot, callback_data=f"{PREFIX}:slot:{slot}") for slot in slots]
        ]
    )


def get_district(idx: str) -> str | None:
    if idx == "other" or not idx.isdigit():
        return None
    i = int(idx)
    names = _districts()
    return names[i] if 0 <= i < len(names) else None
