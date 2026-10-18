"""Справочники районов/массивов по городам.

Район: (каноническое имя, варианты написания). Варианты — в нижнем регистре и
без апострофов (как после offline_parser._norm): кириллица, узбекская латиница
и кириллица. Встроены Фергана (массивы, как их называют риелторы) и Ташкент
(12 административных районов). Для других городов агентство задаёт свои
массивы в боте платформы (/districts) — без выдумок за него.
"""
from __future__ import annotations

import re

District = tuple[str, tuple[str, ...]]

FERGANA: tuple[District, ...] = (
    ("ЭкоСити Аэропорт", ("экосити", "эко сити", "ekositi", "eko siti", "ecocity", "eco city")),
    ("Миндонобод", ("миндонобод", "миндонабад", "mindonobod", "mindonabad")),
    ("Киргули", ("киргули", "кирғули", "қирғули", "қиргули", "qirguli", "kirguli")),
    ("Фрунзе", ("фрунзе", "frunze")),
    ("Ахунбабаев", ("ахунбабаев", "ахунбобоев", "axunboboyev", "axunbabaev", "akhunbabaev")),
    ("Калининский", ("калинин", "kalinin")),
    ("Текстиль", ("текстил", "tekstil")),
    ("Военный городок", ("военн", "voenniy", "voyenniy", "harbiy shaharcha", "ҳарбий шаҳарча", "харбий шахарча")),
    ("Маталка", ("маталк", "matalk")),
    ("Ёрмозор", ("ёрмозор", "ёрмазар", "ермазар", "yormozor", "yormazor")),
    ("Селмаш", ("селмаш", "сельмаш", "selmash")),
    ("Водник", ("водник", "vodnik")),
    ("Дамкуль", ("дамкул", "дамкўл", "damkul", "damkol")),
    ("Юбилейный", ("юбилейн", "yubileyn")),
    ("Ташлак", ("ташлак", "тошлоқ", "тошлок", "toshloq", "toshlok")),
    ("Центр", ("центр", "markaz", "марказ")),
)

TASHKENT: tuple[District, ...] = (
    ("Бектемир", ("бектемир", "bektemir")),
    ("Чиланзар", ("чиланзар", "чилонзор", "chilonzor", "chilanzar")),
    ("Яшнабад", ("яшнабад", "яшнобод", "yashnobod", "yashnabad")),
    ("Мирабад", ("мирабад", "миробод", "mirobod", "mirabad")),
    ("Мирзо-Улугбек", ("улугбек", "улуғбек", "ulugbek")),
    ("Сергели", ("сергели", "sergeli")),
    ("Шайхантахур", ("шайхантахур", "шайхонтохур", "шайхонтоҳур", "shayxontohur", "shaykhantakhur")),
    ("Алмазар", ("алмазар", "олмазор", "olmazor", "almazar")),
    ("Учтепа", ("учтепа", "uchtepa")),
    ("Яккасарай", ("яккасарай", "яккасарой", "yakkasaroy", "yakkasaray")),
    ("Юнусабад", ("юнусабад", "юнусобод", "yunusobod", "yunusabad")),
    ("Янгихаёт", ("янгихаёт", "янгиҳаёт", "yangihayot")),
)

CITY_DISTRICTS: dict[str, tuple[District, ...]] = {"Фергана": FERGANA, "Ташкент": TASHKENT}

MAX_DISTRICTS = 24          # кнопок в фильтре каталога — разумный предел
_AP = re.compile(r"[ʻʼ’`']")


def _alias(text: str) -> str:
    return _AP.sub("", text.lower()).strip()


def for_city(city: str | None) -> tuple[District, ...]:
    return CITY_DISTRICTS.get((city or "").strip(), ())


def parse(text: str) -> tuple[District, ...]:
    """Список агентства: по строке на массив, «Имя: вариант, вариант» (варианты — необязательно).

    Само имя всегда — вариант написания. Повторы и пустые строки пропускаются.
    """
    result: list[District] = []
    seen: set[str] = set()
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        name = " ".join(name.split())[:64]
        if not name or _alias(name) in seen:
            continue
        aliases = [_alias(name)] + [_alias(a) for a in rest.split(",") if len(_alias(a)) >= 3]
        seen.add(_alias(name))
        result.append((name, tuple(dict.fromkeys(aliases))))
        if len(result) == MAX_DISTRICTS:
            break
    return tuple(result)


def format_(districts: tuple[District, ...]) -> str:
    """Обратно в текст (для показа и правки): «Имя: варианты» без самого имени среди вариантов."""
    lines = []
    for name, aliases in districts:
        extra = [a for a in aliases if a != _alias(name)]
        lines.append(f"{name}: {', '.join(extra)}" if extra else name)
    return "\n".join(lines)
