"""Деньги: разбор суммы с валютой из живого ввода и сравнение $ и сумов.

В объявлениях Ферганы цены вперемешку: аренда часто в сумах, продажа — в
долларах. Сравнивать «400» (бюджет) с «3 500 000» (цена) без валюты нельзя —
всё приводится к долларам по курсу USD_RATE (.env, по умолчанию 12 000).
"""
from __future__ import annotations

import re

_USD_MARK = re.compile(r"\$|usd|у\.\s?е|доллар|долл\b|dollar|\bдол\b")
_SUM_MARK = re.compile(r"сум|сўм|so['ʻ’‘`]?m|\bsom\b|\bsum\b|uzs")
_MULT = (
    (re.compile(r"млрд|mlrd|milliard"), 1_000_000_000),
    (re.compile(r"млн|mln|million|миллион|лям|лимон|lyam|limon"), 1_000_000),
    (re.compile(r"тыс|ming|минг|\d\s*[кk]\b"), 1_000),
)
# Без пометки валюты: сумма меньше этого — доллары (никто не пишет бюджет «400 сум»)
USD_GUESS_BELOW = 10_000


def is_usd(currency: str | None) -> bool:
    return bool(currency) and ("$" in currency or currency.strip().lower() in ("usd", "у.е.", "доллар"))


def parse_money(text: str) -> tuple[float, str] | None:
    """«400$», «до 400 у.е.», «3 млн», «3,5 mln so'm», «3 500 000» → (сумма, '$' | 'сум').

    Диапазон («300-400$», «от 300 до 400») → верхняя граница (бюджет «до»).
    """
    low = (text or "").lower().replace(",", ".")
    factor = next((f for pattern, f in _MULT if pattern.search(low)), 1)
    numbers = []
    for raw in re.findall(r"\d+(?:[ .]\d{3})*(?:\.\d+)?", low):
        token = raw.replace(" ", "")
        if factor == 1 and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
            token = token.replace(".", "")  # «45.000» — разряды, а не дробь (но «3.5 млн» — дробь)
        if _is_number(token):
            numbers.append(float(token))
    if not numbers:
        return None
    amount = max(numbers) * factor
    if amount <= 0:
        return None
    if _USD_MARK.search(low):
        return amount, "$"
    if _SUM_MARK.search(low) or amount >= USD_GUESS_BELOW:
        return amount, "сум"
    return amount, "$"


def _is_number(raw: str) -> bool:
    try:
        float(raw)
    except ValueError:
        return False
    return True


def to_usd(amount: float | None, currency: str | None, rate: float) -> float | None:
    if amount is None:
        return None
    return float(amount) if is_usd(currency) else float(amount) / rate
