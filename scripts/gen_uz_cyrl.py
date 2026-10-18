"""Сгенерировать узбекскую кириллицу из узбекской латиницы: bot/i18n_uz_cyrl.py.

Транслитерация — UzTransliterator (правила Е/Ц/Ё/Ю/Я, исключения; MIT,
https://github.com/UlugbekSalaev/UzTransliterator). Боту библиотека не нужна:
результат — обычный словарь в репозитории. Теги, {подстановки}, ссылки,
@имена и /команды не трогаются. У каждой записи — хеш латинского исходника:
тест (tests/test_uz_cyrl.py) падает, если латиницу поменяли, а кириллицу нет.

Запуск после правки узбекских текстов:
    pip install UzTransliterator==2.0.2
    python scripts/gen_uz_cyrl.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for name, value in {"BOT_TOKEN": "1:x", "DATABASE_URL": "sqlite+aiosqlite://", "ADMIN_TELEGRAM_ID": "1"}.items():
    os.environ.setdefault(name, value)

# Неприкосновенное: HTML-теги, {подстановки}, ссылки, @имена, /команды, аббревиатуры и марки
PROTECT = re.compile(r"<[^>]+>|\{[^{}]*\}|https?://\S+|tg://\S+|@\w+|(?<![\w/])/[a-z_]+"
                     r"|\b[A-Z]{2,}|\b(?:Excel|Payme|Click|WhatsApp|YouTube)\b")
# Поправки поверх библиотеки (заимствования, где «ts» = «ц»)
FIXES = {"тсемент": "цемент", "Тсемент": "Цемент"}


def to_cyrillic(text: str, engine) -> str:
    out, pos = [], 0
    for m in PROTECT.finditer(text):
        out.append(_plain(text[pos:m.start()], engine))
        out.append(m.group())
        pos = m.end()
    out.append(_plain(text[pos:], engine))
    return "".join(out)


def _plain(chunk: str, engine) -> str:
    if not re.search(r"[A-Za-z]", chunk):
        return chunk
    # библиотека теряет крайние пробелы/переводы строк — сохраняем их сами
    lead = chunk[: len(chunk) - len(chunk.lstrip())]
    trail = chunk[len(chunk.rstrip()):]
    body = chunk.strip()
    lines = [engine.transliterate(line, from_="lat", to="cyr") if line.strip() else line
             for line in body.split("\n")]
    result = "\n".join(lines)
    for wrong, right in FIXES.items():
        result = result.replace(wrong, right)
    return lead + result + trail


def main() -> None:
    from UzTransliterator.UzTransliterator import UzTransliterator

    from bot import i18n
    from bot.i18n import source_hash
    from bot.platform import texts as platform_texts

    engine = UzTransliterator()
    sections = {"BUTTONS": i18n.BUTTONS, "MESSAGES": i18n.MESSAGES, "PLATFORM": platform_texts.TEXTS}
    lines = ['"""Узбекская кириллица — СГЕНЕРИРОВАНО scripts/gen_uz_cyrl.py из узбекской латиницы.',
             "", "Не править руками: поправьте латиницу (или FIXES в генераторе) и перезапустите.",
             "Формат: ключ -> (хеш латинского исходника — bot.i18n.source_hash, текст).", '"""', ""]
    for section, table in sections.items():
        lines.append(f"{section}: dict[str, tuple[str, str]] = {{")
        for key, variants in table.items():
            uz = variants.get("uz")
            if uz:
                lines.append(f"    {key!r}: ({source_hash(uz)!r}, {to_cyrillic(uz, engine)!r}),")
        lines.append("}")
        lines.append("")
    (ROOT / "bot" / "i18n_uz_cyrl.py").write_text("\n".join(lines), encoding="utf-8")
    print("bot/i18n_uz_cyrl.py:", sum(len(t) for t in sections.values()), "текстов")


if __name__ == "__main__":
    main()
