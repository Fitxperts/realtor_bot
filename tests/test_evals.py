"""Контроль качества разбора: судья (сравнение полей), прогон, отчёт, /ai_eval и порог офлайн-разбора."""
from __future__ import annotations

import asyncio

import pytest

from bot.evals import run as evals

ADMIN = 1


@pytest.mark.parametrize("name,expected,parsed,ok", [
    ("district", "kirguli", {"district": "Qirg'uli"}, True),          # латиница с апострофом
    ("district", "kirguli", {"district": "Қирғули"}, True),           # узбекская кириллица
    ("district", "kirguli", {"address": "Киргули, у базара"}, True),  # район попал в адрес — засчитываем
    ("district", "kirguli", {"district": "Фрунзе"}, False),
    ("phone", "901234501", {"phone": "+998 (90) 123-45-01"}, True),
    ("phone", "901234501", {"phone": "+998 90 123 45 02"}, False),
    ("price", 38000, {"price": "38 000"}, False),                    # строка с пробелом — не число
    ("price", 38000, {"price": 38100}, True),                        # ±1%
    ("price", 38000, {"price": 39000}, False),
    ("rooms", 2, {"rooms": "2"}, True),
    ("currency", "$", {"currency": "сум"}, False),
    ("landmark", ["базар"], {"address": "Возле Киргулийского базара"}, True),
    ("landmark", ["базар"], {"address": None}, False),
])
def test_check_field(name, expected, parsed, ok):
    assert evals.check_field(name, expected, parsed)[0] is ok


def _perfect(cases):
    """Парсер-«отличник»: возвращает ровно ожидаемое (проверяет самого судью)."""
    aliases = {k: v[0] for k, v in evals.DISTRICTS.items()}
    by_text = {}
    for case in cases:
        exp = dict(case["expect"])
        exp["district"] = aliases[exp["district"]]
        if "landmark" in exp:
            exp["address"] = exp.pop("landmark")[0]
        exp["phone"] = "+998" + exp.get("phone", "")
        by_text[case["text"]] = exp

    async def parse(text):
        return by_text[text]
    return parse


async def test_judge_gives_perfect_parser_100():
    cases = evals.load_cases()
    report = await evals.evaluate(_perfect(cases), cases)
    assert report.score == 1.0 and report.perfect_cases == len(cases) == 50 and not report.failures


async def test_failures_and_no_answer_counted():
    cases = evals.load_cases(3)

    async def broken(text):
        if "Киргули" in text:
            raise RuntimeError("провайдер упал")
        return {"deal_type": "sale"}

    report = await evals.evaluate(broken, cases)
    assert report.no_answer == 1 and report.perfect_cases == 0 and report.score < 0.2
    text = evals.format_report(report, title="тест")
    assert "Без ответа ИИ: 1" in text and "#1 · deal_type" in text


async def test_concurrency_is_limited():
    running = peak = 0

    async def slow(text):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return {}

    await evals.evaluate(slow, evals.load_cases(12), concurrency=3)
    assert peak == 3   # не душим бесплатные лимиты провайдера


@pytest.mark.parametrize("path", [evals.DATASET, evals.HOLDOUT])
async def test_offline_parser_quality_floor(path):
    """Запасной разбор без ИИ не должен деградировать (было 65% до переписывания)."""
    report = await evals.evaluate(evals.offline_parse, evals.load_cases(path=path))
    assert report.score >= 0.95, evals.format_report(report, title=str(path))
    for field in ("deal_type", "price", "currency", "phone", "district"):
        ok, total = report.per_field[field]
        assert ok / total >= 0.95, (field, ok, total)


async def test_ai_eval_command(make_harness, monkeypatch):
    from bot.services.ai_service import get_ai_service

    h = await make_harness()

    async def fake_ai(text):          # «ИИ» путает валюту во всех объявлениях
        parsed = get_ai_service()._listing_offline(text)
        parsed["currency"] = "сум" if parsed.get("currency") == "$" else "$"
        return parsed

    monkeypatch.setattr(evals, "ai_parse", fake_ai)
    monkeypatch.setattr(type(get_ai_service()), "enabled", property(lambda self: True))

    await h.message(ADMIN, "/ai_eval 10")
    for _ in range(50):                         # отчёт приходит из фоновой задачи
        if any("Итог" in t for t in h.texts_to(ADMIN)):
            break
        await asyncio.sleep(0.02)
    texts = h.texts_to(ADMIN)
    assert any("Проверяю разбор на 10 объявлениях" in t for t in texts)
    report = next(t for t in texts if "Итог" in t)
    assert "❌ currency: 0/10" in report and "✅ phone: 10/10" in report
    assert "#1 · currency · $ → <code>сум</code>" in report


async def test_ai_eval_without_ai(make_harness, monkeypatch):
    h = await make_harness()
    from bot.services.ai_service import get_ai_service

    monkeypatch.setattr(type(get_ai_service()), "enabled", property(lambda self: False))
    await h.message(ADMIN, "/ai_eval")
    assert "ИИ не настроен" in h.texts_to(ADMIN)[-1]
