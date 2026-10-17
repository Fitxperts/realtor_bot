"""Разбор объявления без ИИ — запасной путь, когда все ИИ-провайдеры недоступны.

Понимает русский, узбекскую латиницу и кириллицу, сленг и типичные сокращения
ферганских каналов. Качество меряется прогоном `python -m bot.evals.run --offline`
(50 объявлений с ожидаемыми полями) — правила здесь общие, не под конкретные тексты.
"""
from __future__ import annotations

import re

from bot.utils.money import parse_money

_AP = "['ʻ’‘`ʼ]"  # апостроф в узбекской латинице пишут по-разному

# --- телефон: узбекский мобильный с +998 или без, с любыми разделителями ---
_PHONE_UZ = re.compile(r"(?<!\d)(?:\+?998[\s\-]?)?\(?(?:9\d|33|50|55|77|88|20)\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)")
_PHONE_ANY = re.compile(r"\+\d[\d\s\-()]{7,}\d|(?<!\d)\d{9,12}(?!\d)")

# --- тип сделки ---
_RENT = re.compile(r"аренд|ижара|сда[её]т|сдам|сдаю|ijara|rent|в\s+месяц|/\s*мес|oyiga|ойига|ойлик|oylik")
_SALE = re.compile(r"прода|прадаю|сотув|сотил|сотиш|sotuv|sotil|sotaman|sale")

# --- вид объекта (по приоритету: явные слова сильнее косвенных). «Квартира» сильнее
# «магазина» (магазин часто — ориентир), «сотки» без других слов — значит участок ---
_KINDS = (
    ("apartment", re.compile(r"квартир|kvartir|kvartr|двушк|тр[её]шк|однушк|новостро")),
    ("land", re.compile(r"участ|uchast|\bер\b|\byer\b|земл")),
    ("commercial", re.compile(rf"офис|ofis|магазин|do{_AP}?kon|дўкон|дукон|помещени|коммерч|тижорат|tijorat|склад|sklad|кафе")),
    ("house", re.compile(r"\bдом(?:а|ик)?\b|ҳовли|ховли|hovli|\bуй\b|\buy\b|коттедж|частн")),
    ("land", re.compile(r"соток|сотки|сотих|sotix|sotka")),
)

# --- комнаты ---
_ROOMS = re.compile(r"(?<![\d/])(\d{1,2})\s*-?\s*(?:комн|ком(?:\.|(?![а-яё]))|хонали|хона|xonali|xona|х(?![а-яё])|к(?![а-яёa-z])|x(?![a-z]))")
_ROOM_WORDS = {"однушк": 1, "двушк": 2, "трешк": 3, "трёшк": 3}

# --- этаж / этажность / площадь / сотки ---
_FLOOR_PAIR = re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])(?!\s*(?:дол|ulush|qism))")
_FLOOR = re.compile(r"(?<![\d/])(\d{1,2})\s*-?\s*(?:й|я|chi|чи)?\s*-?\s*(?:этаж(?!н)|эт\b|эт\.|qavat(?!li)|қават(?!ли)|kavat(?!li))")
_FLOORS_OF = re.compile(r"(?:этаж|эт\.?|qavat|қават)\s*из\s*(\d{1,2})|\bиз\s*(\d{1,2})\s*(?:этаж|эт)")
_FLOORS = re.compile(r"(\d{1,2})\s*-?\s*(?:qavatli|қаватли|этажн)")
_AREA = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:м²|м2|m²|m2|кв\.?\s?м\.?|kv\.?\s?m\.?|квм|кв(?![а-яё])|kv(?![a-z]))")
_SOTKA = re.compile(r"(\d+)\s*(?:сот(?:ок|ки|их|ых)?|sotix|sotka)")
_DATE = re.compile(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}")

# --- цена: число + множитель + валюта ---
_NUM = r"\d+(?:[ .,]\d{3})*(?:[.,]\d+)?"
_MULT_WORD = r"млн|mln|миллион|лям|lyam|limon|ming|минг|тыс\.?|к|k"
_CUR_WORD = rf"\$|у\.?\s?е\.?|y\.\s?e\.?|долл\w*|dollar\w*|бакс\w*|usd|сум|сўм|so{_AP}?m\b|som\b"
_MONEY = re.compile(
    rf"(\$\s*)?({_NUM})(?:\s*({_MULT_WORD})(?![а-яёa-z]))?(?:\s*(\d+)\s*(?:ming|минг|тыс)\.?)?(?:\s*({_CUR_WORD}))?"
)
_USD_WORD = re.compile(r"\$|у\.?\s?е|y\.\s?e|долл|dollar|бакс|usd")

# --- районы Ферганы: любое написание → каноническое (как в выборе массива) ---
DISTRICT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ЭкоСити Аэропорт", ("экосити", "эко сити", "ekositi", "eko siti", "ecocity", "eco city")),
    ("Миндонобод", ("миндонобод", "миндонабад", "mindonobod", "mindonabad")),
    ("Киргули", ("киргули", "кирғули", "қирғули", "қиргули", "qirguli", "kirguli")),
    ("Фрунзе", ("фрунзе", "frunze")),
    ("Ахунбабаев", ("ахунбабаев", "ахунбобоев", "axunboboyev", "axunbabaev", "akhunbabaev")),
    ("Калининский", ("калинин", "kalinin")),
    ("Текстиль", ("текстил", "tekstil")),
    ("Военный городок", ("военн", "voenniy", "voyenniy", "harbiy shaharcha", "ҳарбий шаҳарча", "харбий шахарча")),
    ("Маталка", ("маталк", "matalk")),
    ("Центр", ("центр", "markaz", "марказ")),
)

# Слова, которые не бывают районом (для запасной эвристики «первое значимое слово»)
_STOPWORDS = (
    "аренд", "ижара", "сдам", "сдается", "сдаётся", "продаж", "сотув", "продам", "сотилади", "хозяин",
    "срочно", "комн", "хона", "сум", "сўм", "дом", "квартир", "участ", "коммерч", "тижорат", "офис",
    "этаж", "кв", "прода", "прада", "сдаё", "сдаю", "куп", "торг", "цена", "тел", "соток", "сотих",
    "двушк", "трешк", "трёшк", "однушк", "ремонт", "мебел", "техник", "новострой", "ипотек", "звон",
    "ассалом", "помещени", "магазин", "документ", "нарх", "мўлжал", "ориентир", "возле", "рядом",
    "ijara", "sotuv", "sotil", "sotaman", "kvartir", "kvartr", "hovli", "uchast", "xona", "xonali", "egasi",
    "shoshilinch", "som", "sum", "uy", "yer", "ofis", "tijorat", "qavat", "narx", "sotix", "sroch",
    "kelish", "remont", "mebel", "texnika", "moljal", "tel", "mahalla",
    "rent", "sale", "owner", "room", "apartment", "house", "land", "commercial", "floor",
)

# Ориентир: «Ориентир: …», «Mo'ljal: …», «возле/напротив … », «… yonida/ro'parasida»
_LANDMARK_MARKED = re.compile(rf"(?:ориентир|мўлжал|mo{_AP}?ljal|адрес|manzil|манзил)\s*[:\-–]\s*([^\n.;!]+)", re.I)
_LANDMARK_RU = re.compile(
    r"((?i:возле|рядом\s+с|напротив|около|недалеко\s+от|за)\s+[^,.;!\n]{3,60}|\bу\s+[А-ЯЁ][^,.;!\n]{2,60})"
)
_LANDMARK_UZ = re.compile(
    rf"([^,.;!\n:]{{3,60}}?\s+(?:yonida|ёнида|ro{_AP}?parasida|рўпарасида|orqasida|орқасида|yaqinida|яқинида|bo{_AP}?yida|бўйида))",
    re.I,
)


def _norm(text: str) -> str:
    return re.sub(_AP, "", text.lower())


def _strip(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(" ", text)


def _phone(text: str) -> tuple[str | None, str]:
    """Первый телефон и текст без ВСЕХ телефонов (второй номер не станет ценой)."""
    for pattern in (_PHONE_UZ, _PHONE_ANY):
        matches = list(pattern.finditer(text))
        if matches:
            return matches[0].group().strip(), pattern.sub(" ", text)
    return None, text


def _kind(low: str) -> str | None:
    for kind, pattern in _KINDS:
        if pattern.search(low):
            return kind
    return None


def _price(low: str) -> tuple[float, str] | None:
    """Цена: сначала сумма с валютой, затем с множителем («3 млн»), затем самое большое число."""
    best_cur = best_mult = best_plain = None
    for m in _MONEY.finditer(low):
        dollar_sign, number, mult, extra, currency = m.groups()
        snippet = " ".join(x for x in (number, mult, currency) if x)
        if dollar_sign:
            snippet += " $"
        parsed = parse_money(snippet)
        if parsed is None:
            continue
        amount, cur = parsed
        if extra and mult:  # «2 mln 800 ming» = 2 800 000
            amount += float(extra) * 1_000
        if currency or dollar_sign:
            cur = "$" if (dollar_sign or _USD_WORD.search(currency or "")) else "сум"
            best_cur = best_cur or (amount, cur)
        elif mult:
            best_mult = best_mult or (amount, cur)
        elif amount >= 50 and (best_plain is None or amount > best_plain[0]):
            best_plain = (amount, cur)
    return best_cur or best_mult or best_plain


def _district(text: str, low: str) -> str | None:
    norm = _norm(low)
    hits = [(norm.find(alias), name) for name, aliases in DISTRICT_ALIASES for alias in aliases if alias in norm]
    if hits:
        return min(hits)[1]  # упомянутый первым
    # Неизвестное место (другой город): первое значимое слово — как писал автор
    for token in re.findall(r"[a-zа-яёўқғҳ][a-zа-яёўқғҳʻ’']{2,}", low):
        clean = token.strip("ʻ’'")
        if len(clean) >= 3 and not any(_norm(clean).startswith(sw) for sw in _STOPWORDS):
            return clean.capitalize()
    return None


def _landmarks(text: str) -> str | None:
    found: list[str] = []
    for pattern in (_LANDMARK_MARKED, _LANDMARK_RU, _LANDMARK_UZ):
        for m in pattern.finditer(text):
            piece = m.group(1).strip(" -–:")
            if piece and not any(piece in f or f in piece for f in found):
                found.append(piece)
    return ", ".join(found)[:255] or None


def parse(text: str) -> dict:
    """Поля объявления из текста: тип сделки, вид, комнаты, этаж, площадь, цена, валюта,
    район, ориентир (address), телефон. Отсутствующее — не возвращается."""
    result: dict = {}
    phone, work = _phone(text or "")
    if phone:
        result["phone"] = phone
    low = work.lower()

    kind = _kind(low)
    if _RENT.search(low):
        result["deal_type"] = "rent"
    elif _SALE.search(low) or kind == "land":  # участок почти всегда продают
        result["deal_type"] = "sale"

    rooms = _ROOMS.search(low)
    if rooms:
        result["rooms"] = int(rooms.group(1))
    else:
        for word, n in _ROOM_WORDS.items():
            if word in low:
                result["rooms"] = n
    if kind is None and "rooms" in result:
        kind = "apartment"  # «2к, 300$» — по умолчанию квартира
    if kind:
        result["property_kind"] = kind

    pair = _FLOOR_PAIR.search(low)
    if pair and 0 < int(pair.group(1)) <= int(pair.group(2)) <= 40:
        result["floor"], result["floors"] = int(pair.group(1)), int(pair.group(2))
    else:
        floor = _FLOOR.search(low)
        if floor and 0 < int(floor.group(1)) <= 40:
            result["floor"] = int(floor.group(1))
        floors = _FLOORS_OF.search(low) or _FLOORS.search(low)
        if floors:
            value = int(next(g for g in floors.groups() if g))
            if value <= 40 and value >= result.get("floor", 0):
                result["floors"] = value

    area = _AREA.search(low)
    if area:
        result["area"] = float(area.group(1).replace(",", "."))

    # Цена — после того, как убраны комнаты, этажи, площадь и сотки
    priceless = low
    for pattern in (_DATE, _ROOMS, _FLOOR_PAIR, _FLOOR, _FLOORS_OF, _FLOORS, _AREA, _SOTKA):
        priceless = _strip(priceless, pattern)
    price = _price(priceless)
    if price:
        result["price"], result["currency"] = price

    district = _district(work, low)
    if district:
        result["district"] = district
    address = _landmarks(work)
    if address:
        result["address"] = address
    return result
