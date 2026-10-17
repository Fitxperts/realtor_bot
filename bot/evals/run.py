"""Контроль качества разбора объявлений: 50 объявлений с ожидаемыми полями.

Зачем: провайдеры меняют и снимают модели — ошибки в цене, валюте и районе
должны быть видны до клиентов. Прогон считает точность по каждому полю и
показывает, где именно модель ошиблась.

    python -m bot.evals.run              # через цепочку ИИ (как в боте)
    python -m bot.evals.run --offline    # запасной разбор без ИИ (регулярки)
    python -m bot.evals.run --limit 10 --min-score 0.85

В боте: админ-команда /ai_eval [N]. Объявления в listings.json — примеры в стиле
ферганских каналов; добавляйте реальные — тем точнее проверка.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

DATASET = Path(__file__).with_name("listings.json")
# Контрольная выборка: написана отдельно и прогнана один раз до правок (офлайн-разбор: 98%,
# 13/15 без ошибок). Правила под неё не подгоняем — для честной оценки добавляйте новые тексты
HOLDOUT = Path(__file__).with_name("holdout.json")

# Районы пишут по-разному: кириллица, латиница, сокращения — любой вариант засчитываем
DISTRICTS: dict[str, list[str]] = {
    "kirguli": ["киргули", "қирғули", "кирғули", "қиргули", "qirguli", "kirguli"],
    "mindonobod": ["миндонобод", "миндонабад", "mindonobod", "mindonabad"],
    "frunze": ["фрунзе", "frunze"],
    "axunboboyev": ["ахунбабаев", "ахунбобоев", "axunboboyev", "axunbabaev", "akhunbabaev"],
    "kalinin": ["калинин", "kalinin"],
    "tekstil": ["текстил", "tekstil"],
    "voenniy": ["военн", "voenn", "voyenn", "harbiy", "ҳарбий", "харбий"],
    "matalka": ["маталк", "matalk"],
    "ekositi": ["экосити", "эко сити", "ekositi", "eko siti", "ecocity", "eco city", "аэропорт", "aeroport"],
    "markaz": ["центр", "марказ", "markaz", "center", "ахунбабаев", "axunboboyev"],
}
FIELDS = ("deal_type", "property_kind", "rooms", "floor", "floors", "area", "price", "currency",
          "district", "phone", "landmark")

ParseFn = Callable[[str], Awaitable[dict]]


def _norm(text) -> str:
    return re.sub(r"[ʻ’'`ʼ‘]", "", str(text or "")).lower()


def _digits(text) -> str:
    return re.sub(r"\D", "", str(text or ""))


def check_field(name: str, expected, parsed: dict) -> tuple[bool, object]:
    """(верно ли, что получили) для одного поля."""
    if name == "district":
        got = " ".join(str(parsed.get(k) or "") for k in ("district", "address"))
        return any(alias in _norm(got) for alias in DISTRICTS[expected]), parsed.get("district")
    if name == "landmark":  # ориентир должен попасть в адрес, а не потеряться
        got = parsed.get("address")
        return any(word in _norm(got) for word in expected), got
    got = parsed.get(name)
    if name == "phone":
        return _digits(got)[-9:] == expected, got
    if name in ("price", "area"):
        try:
            return abs(float(got) - expected) <= max(1.0, expected * 0.01), got
        except (TypeError, ValueError):
            return False, got
    if name in ("rooms", "floor", "floors"):
        try:
            return int(got) == expected, got
        except (TypeError, ValueError):
            return False, got
    return got == expected, got


@dataclass
class Failure:
    case_id: int
    field: str
    expected: object
    got: object


@dataclass
class Report:
    total_cases: int = 0
    perfect_cases: int = 0
    no_answer: int = 0                      # ИИ не ответил (вся цепочка упала / пустой разбор)
    seconds: float = 0.0
    per_field: dict[str, list[int]] = field(default_factory=dict)   # поле → [верно, всего]
    failures: list[Failure] = field(default_factory=list)

    @property
    def score(self) -> float:
        ok = sum(v[0] for v in self.per_field.values())
        total = sum(v[1] for v in self.per_field.values())
        return ok / total if total else 0.0


def load_cases(limit: int | None = None, *, path: Path = DATASET) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    return cases[:limit] if limit else cases


def score_case(case: dict, parsed: dict, report: Report) -> None:
    report.total_cases += 1
    if not parsed:
        report.no_answer += 1
    perfect = True
    for name, expected in case["expect"].items():
        ok, got = check_field(name, expected, parsed)
        stat = report.per_field.setdefault(name, [0, 0])
        stat[0] += ok
        stat[1] += 1
        if not ok:
            perfect = False
            report.failures.append(Failure(case["id"], name, expected, got))
    report.perfect_cases += perfect


async def evaluate(parse: ParseFn, cases: list[dict], *, concurrency: int = 3) -> Report:
    """Прогнать объявления через parse (не больше `concurrency` запросов одновременно)."""
    report = Report()
    semaphore = asyncio.Semaphore(concurrency)
    started = time.monotonic()

    async def one(case: dict) -> tuple[dict, dict]:
        async with semaphore:
            try:
                return case, await parse(case["text"]) or {}
            except Exception:  # noqa: BLE001 - сбой одного объявления — это «нет ответа»
                return case, {}

    for case, parsed in sorted(await asyncio.gather(*(one(c) for c in cases)), key=lambda r: r[0]["id"]):
        score_case(case, parsed, report)
    report.seconds = time.monotonic() - started
    return report


def format_report(report: Report, *, title: str, max_failures: int = 15) -> str:
    """Отчёт (HTML для Telegram): общий балл, точность по полям, первые ошибки."""
    lines = [f"🧪 <b>{escape(title)}</b>",
             f"Итог: <b>{report.score:.0%}</b> · без ошибок {report.perfect_cases}/{report.total_cases} "
             f"· {report.seconds:.0f} с"]
    if report.no_answer:
        lines.append(f"⚠️ Без ответа ИИ: {report.no_answer} (сработал бы офлайн-разбор)")
    lines.append("")
    for name in FIELDS:
        if name in report.per_field:
            ok, total = report.per_field[name]
            mark = "✅" if ok == total else ("🟡" if ok / total >= 0.8 else "❌")
            lines.append(f"{mark} {name}: {ok}/{total}")
    if report.failures:
        lines += ["", "<b>Ошибки</b> (№ · поле · ждали → получили):"]
        for f in report.failures[:max_failures]:
            lines.append(f"#{f.case_id} · {f.field} · {escape(str(f.expected))} → "
                         f"<code>{escape(str(f.got))[:60]}</code>")
        if len(report.failures) > max_failures:
            lines.append(f"… и ещё {len(report.failures) - max_failures}")
    return "\n".join(lines)


async def ai_parse(text: str) -> dict:
    """Разбор так же, как в боте, но БЕЗ подмены офлайн-разбором — проверяем именно ИИ."""
    from bot.services.ai_service import get_ai_service

    return await get_ai_service()._listing_with_ai(text)


async def offline_parse(text: str) -> dict:
    from bot.services.ai_service import get_ai_service

    return get_ai_service()._listing_offline(text)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Проверка качества разбора объявлений")
    parser.add_argument("--offline", action="store_true", help="запасной разбор без ИИ")
    parser.add_argument("--limit", type=int, default=None, help="только первые N объявлений")
    parser.add_argument("--min-score", type=float, default=0.0, help="код возврата 1, если итог ниже")
    parser.add_argument("--holdout", action="store_true", help="контрольная выборка (holdout.json)")
    args = parser.parse_args()

    cases = load_cases(args.limit, path=HOLDOUT if args.holdout else DATASET)
    if args.offline:
        report = await evaluate(offline_parse, cases)
        title = "Офлайн-разбор (без ИИ)"
    else:
        from bot.services.ai_service import get_ai_service

        if not get_ai_service().enabled:
            print("ИИ не настроен (нет ключей) — запустите с --offline или пропишите ключи в .env")
            return 2
        report = await evaluate(ai_parse, cases)
        title = "Разбор ИИ"
    print(_strip_html(format_report(report, title=title, max_failures=50)))
    return 0 if report.score >= args.min_score else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
