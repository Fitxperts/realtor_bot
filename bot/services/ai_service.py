"""ИИ-сервис РиелторБота.

Три функции:
- generate_description(property_data) -> str  — готовое объявление (MarkdownV2,
  строгий формат с премиум-эмодзи), для публикации в канал;
- parse_free_text(text) -> dict               — разбор свободного текста
  собственника («2к чиланзар 450 хозяин») в структуру;
- find_matches(client, properties) -> list    — умный подбор объектов под
  клиента (бюджет ±10%, район, комнаты).

Дополнительно generate_short_description() — короткое описание для карточек.
Совместимо с anthropic 0.28 (базовый messages.create, без thinking/output_config).
"""
from __future__ import annotations

import json
import logging
import re

from bot.config import (
    AGENCY_CONTACTS,
    BOT_PUBLISH_URL,
    CHANNEL_URL,
    PREMIUM_EMOJI,
    get_settings,
)
from bot.services.llm_providers import LLMRouter, build_chain

logger = logging.getLogger(__name__)

# Узбекские подписи
_KIND_UZ = {
    "apartment": "КВАРТИРА",
    "house": "ҲОВЛИ",
    "land": "ЕР УЧАСТКА",
    "commercial": "ТИЖОРАТ ОБЪЕКТ",
}
_KIND_RU = {
    "apartment": "квартиру",
    "house": "дом",
    "land": "участок",
    "commercial": "коммерческое помещение",
}
# Хештег вида недвижимости для поста в канал (вместо #Фарғона)
_KIND_HASHTAG = {
    "apartment": "Квартира",
    "house": "Ҳовли",
    "land": "Ер",
    "commercial": "Тижорат",
}
_DEAL_RU = {"rent": "аренду", "sale": "продажу"}

_MD_SPECIALS = r"_*[]()~`>#+-=|{}.!\\"


def escape_md(text: str) -> str:
    """Экранировать спецсимволы MarkdownV2."""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", str(text))


def _fmt_price(value) -> str:
    try:
        return f"{int(float(value)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


class AIService:
    def __init__(self) -> None:
        # Цепочка провайдеров с автопереключением: упал один (снята модель, лимит,
        # сеть) — отвечает следующий. Офлайн-регулярки — только если упали ВСЕ.
        self.router = LLMRouter(build_chain(get_settings()), label="text")

    @property
    def enabled(self) -> bool:
        return self.router.enabled

    async def _chat(self, prompt: str, *, system: str | None = None, max_tokens: int = 500) -> str | None:
        """Единый вызов LLM по цепочке провайдеров. None — если ИИ недоступен."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.router.complete(messages, max_tokens=max_tokens)

    # ------------------------------------------------------------------
    # 1. Готовое объявление (строгий формат, MarkdownV2)
    # ------------------------------------------------------------------
    @staticmethod
    def _emoji(base: str, key: str) -> str:
        """Премиум-эмодзи, если задан реальный ID, иначе обычный эмодзи."""
        eid = PREMIUM_EMOJI.get(key)
        if eid and eid not in ("0", ""):
            return f"![{base}](tg://emoji?id={eid})"
        return base

    def generate_description(self, property_data: dict) -> str:
        d = property_data
        em = self._emoji
        deal = d.get("deal_type") or d.get("type")
        kind = d.get("property_kind")
        kind_uz = _KIND_UZ.get(kind, "КВАРТИРА")
        title_deal = "ИЖАРАГА БЕРИЛАДИ" if deal == "rent" else "СОТИЛАДИ"
        tag_deal = "\\#Ижара" if deal == "rent" else "\\#Сотув"
        tag_kind = _KIND_HASHTAG.get(kind, "Квартира")
        rooms = d.get("rooms")

        lines: list[str] = []
        lines.append(f"{em('🏢', 'building')} *{kind_uz} {title_deal}*")

        # Хештеги: тип сделки + вид недвижимости (все объекты и так по Фергане)
        tags = f"{tag_deal} \\#{tag_kind}"
        if rooms:
            tags += f" \\#{rooms}\\_хона"
        if d.get("urgent"):
            tags += f" {em('🚨', 'siren')} \\#Срочно"
        lines.append(tags)
        lines.append("")

        # Манзил: массив/район + ориентир (что прислали). Прочерк — только если
        # вообще ничего нет.
        loc_parts = [p for p in (d.get("district"), d.get("address")) if p]
        address = escape_md(", ".join(loc_parts) or "—")
        lines.append(f"{em('📍', 'pin')} *Манзил:* {address}\\.")
        lines.append("")

        if rooms:
            floor, floors = d.get("floor"), d.get("floors")
            fl = f" \\({floor}/{floors} қават\\)" if floor and floors else ""
            lines.append(f"{em('🚪', 'door')} *Хоналар сони:* {rooms} хона{fl}\\.")

        reno = escape_md(d.get("renovation") or "Аъло даражада, тоза ва шинам")
        lines.append(f"{em('🛠', 'tools')} *Ҳолати:* {reno}\\.")

        comforts = self._comforts(d)
        lines.append(f"{em('⚡️', 'lightning')} *Қулайликлари:* {comforts}\\.")
        lines.append("")

        price = escape_md(_fmt_price(d.get("price")))
        neg = " \\(келишилади\\)" if d.get("negotiable") else ""
        lines.append(f"{em('💰', 'money')} *Нархи:* {price} сўм{neg}\\.")
        lines.append("_\\*Риэлторлик хизмати алоҳида\\._")
        lines.append("")

        lines.append(f"{em('📞', 'phone')} *Мурожаат учун:*")
        # Номера показываем открыто (без спойлера) — сразу видны и кликабельны.
        contacts = "\n".join(
            f"☎️ {escape_md(phone)} \\| @{escape_md(username)}"
            for phone, username in AGENCY_CONTACTS
        )
        lines.append(contacts)
        lines.append("")

        lines.append("➖➖➖➖➖➖➖➖➖➖")
        lines.append(f"📢 [«Фарғона Уйлари» каналига обуна бўлинг]({CHANNEL_URL})")
        lines.append(f"📥 [Ботда бепул эълон жойлаш]({BOT_PUBLISH_URL})")
        return "\n".join(lines)

    @staticmethod
    def _comforts(d: dict) -> str:
        present = [
            name
            for name, key in (("Газ", "gas"), ("сув", "water"), ("свет", "electricity"), ("интернет", "internet"))
            if d.get(key)
        ]
        if not present:
            return "маълумот сўраб олинг"
        if len(present) >= 3:
            return ", ".join(present[:-1]) + f" ва {present[-1]} узлуксиз"
        return " ва ".join(present) + " бор"

    # ------------------------------------------------------------------
    # 2. Разбор свободного текста собственника
    # ------------------------------------------------------------------
    async def parse_free_text(self, text: str) -> dict:
        if self.enabled:
            parsed = await self._parse_with_ai(text)
            if parsed:
                return parsed
        return self._parse_regex(text)

    # ------------------------------------------------------------------
    # 2b. Разбор ПОЛНОГО объявления (своего или готового от партнёра)
    # ------------------------------------------------------------------
    _LISTING_KEYS = (
        "deal_type", "property_kind", "rooms", "district", "address",
        "area", "floor", "floors", "price", "currency", "phone", "description",
    )

    async def parse_listing(self, text: str) -> dict:
        """Разобрать длинное объявление в структуру + причесать описание.

        Основной путь размещения: пользователь присылает одно сообщение
        (своё или готовое объявление партнёра), бот сам извлекает поля и
        оформляет. При недоступности ИИ — офлайн-разбор регулярками.
        """
        if self.enabled:
            data = await self._listing_with_ai(text)
            if data:
                return data
        return self._listing_offline(text)

    async def _listing_with_ai(self, text: str) -> dict:
        prompt = (
            "Ты помощник риелторского агентства в Фергане (Узбекистан). Люди пишут "
            "и говорят НЕ на литературном языке: смесь узбекского, русского и "
            "узбекской латиницы, сокращения, сленг, опечатки, голосовой набор, "
            "шутки и лишние слова. Всё это надо ПОНЯТЬ и извлечь данные — не "
            "теряй смысл из-за формы.\n\n"
            "Верни JSON со строго такими полями:\n"
            "deal_type (rent — аренда/ижара/ижарага/сдаётся; "
            "sale — продажа/сотув/сотилади/продаётся),\n"
            "property_kind (apartment — квартира/кв/хона; house — дом/ховли/уй; "
            "land — участок/ер; commercial — офис/тижорат/магазин),\n"
            "rooms (целое; '2х','2к','2 хона','двушка' → 2),\n"
            "district (район/массив — как называют место),\n"
            "address — адрес ИЛИ ЛЮБОЙ ОРИЕНТИР как в тексте: 'возле базара', "
            "'за рестораном X', 'рядом с ЖК/домом Y', 'напротив школы', название "
            "дома/остановки/компании. Если есть хоть какой-то намёк на место — "
            "ОБЯЗАТЕЛЬНО занеси его сюда, НЕ оставляй пустым и НЕ выдумывай новое,\n"
            "area (число, м²), floor (этаж, целое), floors (этажность, целое),\n"
            "price (ЧИСЛО без пробелов и валюты; '67.000' и '67 000' → 67000; "
            "'лям'/'млн'/'миллион' → миллионы, 'к'/'минг'/'тыс' → тысячи),\n"
            "currency ('$' если доллар/у.е/$/USD; иначе 'сум'),\n"
            "phone (строка),\n"
            "description — аккуратное описание объекта в деловом стиле агентства "
            "на русском (2–4 предложения), без телефонов, ссылок, мусорных эмодзи "
            "и рекламы; опечатки и сленг исправь на нормальный язык.\n"
            "Отсутствующее поле — null. Верни ТОЛЬКО JSON.\n\nОбъявление:\n" + text
        )
        raw = await self._chat(prompt, max_tokens=800)
        if raw:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    return self._normalize_listing(json.loads(match.group()))
                except (ValueError, TypeError):
                    logger.warning("ИИ вернул невалидный JSON при разборе объявления")
        return {}

    def _listing_offline(self, text: str) -> dict:
        data = self._parse_regex(text)
        data["description"] = self._clean_description(text, data.get("phone"))
        return data

    @classmethod
    def _normalize_listing(cls, data: dict) -> dict:
        result: dict = {}
        for key in cls._LISTING_KEYS:
            value = data.get(key)
            if value in (None, "", "null"):
                continue
            if key in ("rooms", "floor", "floors"):
                try:
                    result[key] = int(value)
                except (TypeError, ValueError):
                    continue
            elif key in ("area", "price"):
                try:
                    result[key] = float(str(value).replace(" ", "").replace(",", "."))
                except (TypeError, ValueError):
                    continue
            elif key == "currency":
                low = str(value).lower()
                result[key] = "$" if any(s in low for s in ("$", "usd", "доллар", "у.е")) else "сум"
            elif key == "phone":
                # берём ПЕРВЫЙ телефон (в объявлении их может быть несколько) и режем под лимит БД
                match = re.search(r"\+?\d[\d\s\-()]{6,}\d", str(value))
                result[key] = (match.group().strip() if match else str(value).strip())[:32]
            else:
                # режем строковые поля под лимиты колонок БД (varchar)
                limit = {"district": 128, "address": 255, "description": 4000}.get(key)
                cleaned = str(value).strip()
                result[key] = cleaned[:limit] if limit else cleaned
        return result

    @staticmethod
    def _clean_description(text: str, phone: str | None) -> str:
        cleaned = text
        if phone:
            cleaned = cleaned.replace(phone, " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:800]

    async def _parse_with_ai(self, text: str) -> dict:
        prompt = (
            "Разбери объявление о недвижимости в JSON с полями: deal_type "
            "(rent/sale), property_kind (apartment/house/land/commercial), rooms "
            "(int), district (str), price (число), phone (str). Отсутствующее — null. "
            "Верни только JSON.\n\nТекст: " + text
        )
        raw = await self._chat(prompt, max_tokens=300)
        if raw:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                    return {k: v for k, v in data.items() if v is not None}
                except (ValueError, TypeError):
                    logger.warning("ИИ вернул невалидный JSON при разборе текста")
        return {}

    # Стоп-слова (префиксы) для отсева служебных слов при поиске района.
    # Русские + узбекская латиница + английские.
    _STOPWORDS = {
        # RU
        "аренд", "ижара", "сдам", "сдается", "сдаётся", "продаж", "сотув",
        "продам", "сотилади", "хозяин", "срочно", "комн", "хона", "сум", "сўм",
        "дом", "квартира", "участок", "коммерч", "тижорат", "офис", "этаж", "кв",
        "прода", "сдаё", "сдаю", "куп", "торг", "цена", "тел",
        # UZ (latin)
        "ijara", "sotuv", "sotil", "sotaman", "kvartira", "hovli", "uchastka",
        "xona", "xonali", "egasi", "shoshilinch", "som", "sum", "uy", "yer",
        "ofis", "tijorat", "qavat", "narx",
        # EN
        "rent", "sale", "owner", "room", "apartment", "house", "land",
        "commercial", "floor",
    }
    _KIND_WORDS = (
        ("apartment", ("квартир", "кв", "хона", "kvartira", "xona", "apartment")),
        ("house", ("дом", "ҳовли", "уй", "hovli", "uy", "house")),
        ("land", ("участок", "ер", "uchastka", "yer", "land")),
        ("commercial", ("коммерч", "тижорат", "офис", "tijorat", "ofis", "commercial")),
    )
    _RENT_WORDS = ("аренд", "ижара", "сдам", "сдаё", "сдаю", "сдается", "ижарага",
                   "ijara", "rent")
    _SALE_WORDS = ("прода", "сотув", "сотил", "сотиш", "sotuv", "sotil",
                   "sotaman", "sale")

    @classmethod
    def _parse_regex(cls, text: str) -> dict:
        result: dict = {}

        # Телефон — сначала номер с «+» (допускаем пробелы/скобки), иначе
        # непрерывная последовательность 9–12 цифр. Так цену «65 000 000»
        # (разбита пробелами по 3) не примем за телефон.
        phone = re.search(r"\+\d[\d\s\-()]{7,}\d", text) or re.search(r"(?<!\d)\d{9,12}(?!\d)", text)
        work = text
        if phone:
            result["phone"] = phone.group().strip()
            work = text.replace(phone.group(), " ")
        low = work.lower()

        # Тип сделки (RU / UZ-latin / EN)
        if any(w in low for w in cls._RENT_WORDS):
            result["deal_type"] = "rent"
        elif any(w in low for w in cls._SALE_WORDS):
            result["deal_type"] = "sale"

        # Вид объекта
        for kind, words in cls._KIND_WORDS:
            if any(w in low for w in words):
                result["property_kind"] = kind
                break

        # Комнаты: "2к", "3 комн", "2-х", "2x", "3 xona", "2 xonali"
        rooms = re.search(r"(\d+)\s*[-]?\s*(?:комн|хонали|хона|xonali|xona|к|x)", low)
        if rooms:
            result["rooms"] = int(rooms.group(1))

        # Цена: наибольшее число, учитывая разряды через пробел («65 000 000»)
        numbers: list[int] = []
        for group in re.findall(r"\d[\d ]*\d|\d", low):
            digits = group.replace(" ", "")
            if digits.isdigit() and len(digits) <= 12:
                numbers.append(int(digits))
        if numbers:
            result["price"] = float(max(numbers))

        # Район: первое подходящее слово (кириллица ИЛИ узбекская латиница),
        # не начинающееся со стоп-слова.
        for token in re.findall(r"[a-zа-яёўқғҳ][a-zа-яёўқғҳʻ’']{2,}", low):
            clean = token.strip("ʻ’'")
            if len(clean) < 3 or any(clean.startswith(sw) for sw in cls._STOPWORDS):
                continue
            result["district"] = clean.capitalize()
            break

        return result

    # ------------------------------------------------------------------
    # 3. Умный подбор объектов под клиента
    # ------------------------------------------------------------------
    def find_matches(self, client: dict, properties: list) -> list:
        deal_map = {"rent": "rent", "buy": "sale"}
        want_type = deal_map.get(self._get(client, "deal_type"))
        budget = self._get(client, "budget")
        district = (self._get(client, "district") or "").lower()
        rooms = self._get(client, "rooms")

        scored: list[tuple[int, object]] = []
        for prop in properties:
            if want_type and self._get(prop, "type") not in (want_type, None):
                continue
            price = self._get(prop, "price")
            if budget and price and float(price) > float(budget) * 1.1:
                continue  # бюджет +10%

            score = 0
            p_district = (self._get(prop, "district") or "").lower()
            if district and district in p_district:
                score += 2
            if rooms and self._get(prop, "rooms") == rooms:
                score += 2
            if budget and price and float(price) <= float(budget):
                score += 1
            scored.append((score, prop))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [prop for _, prop in scored]

    @staticmethod
    def _get(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        value = getattr(obj, key, None)
        # enum -> value
        return getattr(value, "value", value)

    # ------------------------------------------------------------------
    # Короткое описание (для карточек в боте)
    # ------------------------------------------------------------------
    async def generate_short_description(self, data: dict) -> str:
        text = await self._chat(
            json.dumps(data, ensure_ascii=False),
            system=(
                "Ты риелтор. Составь короткое привлекательное описание объекта "
                "на русском (2-3 предложения) по данным, без выдумок."
            ),
            max_tokens=300,
        )
        return text or self._short_fallback(data)

    @staticmethod
    def _short_fallback(data: dict) -> str:
        kind = _KIND_RU.get(data.get("property_kind", ""), "объект")
        deal = _DEAL_RU.get(data.get("deal_type", ""), "")
        bits = []
        if data.get("rooms"):
            bits.append(f"{data['rooms']}-комн.")
        if data.get("area"):
            bits.append(f"{data['area']:g} м²")
        if data.get("district"):
            bits.append(f"район {data['district']}")
        line = kind.capitalize()
        if bits:
            line += ", " + ", ".join(bits)
        if deal:
            line += f", на {deal}"
        return line + ". Подробности — у риелтора."


_service: AIService | None = None


def get_ai_service() -> AIService:
    global _service
    if _service is None:
        _service = AIService()
    return _service
