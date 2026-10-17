"""Тесты ИИ-сервиса (офлайн-режимы: без реального Anthropic)."""
import pytest

from bot.services.ai_service import escape_md, get_ai_service


def test_escape_md():
    assert escape_md("а.б(в)") == r"а\.б\(в\)"


def test_generate_description_no_broken_emoji():
    svc = get_ai_service()
    text = svc.generate_description(
        {"deal_type": "rent", "property_kind": "apartment", "address": "Тест",
         "rooms": 2, "price": 2000000, "negotiable": True}
    )
    # с плейсхолдерами ID кастомных эмодзи не должно быть tg://emoji
    assert "tg://emoji" not in text
    assert "🏢" in text
    assert "\\#Ижара" in text
    assert "2 000 000 сўм" in text
    assert "\\." in text  # экранированные точки MarkdownV2


def test_generate_description_dollar_price_and_brand():
    """Цена в долларах не должна публиковаться как «сўм»; подвал — из настроек."""
    from bot import config

    text = get_ai_service().generate_description(
        {"deal_type": "sale", "property_kind": "house", "district": "Qirguli",
         "rooms": 4, "price": 45000, "currency": "$"}
    )
    assert "45 000 $" in text and "сўм" not in text
    assert config.CHANNEL_URL in text and config.AGENCY_CONTACTS[0][0] in text.replace("\\+", "+")

    no_price = get_ai_service().generate_description({"deal_type": "rent", "property_kind": "apartment"})
    assert "*Нархи:* —" in no_price and "— сўм" not in no_price


@pytest.mark.parametrize(
    "text,currency,floor",
    [
        ("Сдаётся 2 комнатная квартира Киргули 3/5 этаж мебель 300$ в месяц", "$", (3, 5)),
        ("продам 3к 45000 у.е. 4/9", "$", (4, 9)),
        ("2 xonali kvartira 400 dollar 2/4 qavat", "$", (2, 4)),
        ("сдаётся 1к центр 2800000 сум", None, None),
        ("продаю 1/2 доли дома", None, None),         # доля — не этаж
        ("сдам с 25/12/2026, 2к", None, None),        # дата — не этаж
    ],
)
def test_offline_currency_and_floor(text, currency, floor):
    parsed = get_ai_service()._parse_regex(text)
    assert parsed.get("currency") == currency
    assert ((parsed["floor"], parsed["floors"]) if "floor" in parsed else None) == floor


def test_parse_contacts():
    from bot.config import _parse_contacts

    assert _parse_contacts("+998901112233:@agent1, +998902223344:agent2") == [
        ("+998901112233", "agent1"), ("+998902223344", "agent2")]
    assert _parse_contacts("") == []


@pytest.mark.parametrize(
    "text,expect",
    [
        ("2к чиланзар 450 хозяин", {"rooms": 2, "district": "Чиланзар", "price": 450.0}),
        ("продам 3 комн дом киргули 90000000", {"deal_type": "sale", "property_kind": "house", "rooms": 3}),
        ("сдаётся 1к центр 200000 +998901112233", {"deal_type": "rent", "phone": "+998901112233"}),
        # узбекская латиница
        ("2x margʻilon 450000 sotuv egasi", {"deal_type": "sale", "rooms": 2, "district": "Margʻilon"}),
        ("3 xonali kvartira ijaraga fargʻona 2500000",
         {"deal_type": "rent", "property_kind": "apartment", "rooms": 3, "district": "Fargʻona"}),
        ("hovli sotiladi qirguli 90000000", {"deal_type": "sale", "property_kind": "house", "district": "Qirguli"}),
    ],
)
def test_parse_free_text(text, expect):
    parsed = get_ai_service()._parse_regex(text)
    for k, v in expect.items():
        assert parsed.get(k) == v


def test_parse_free_text_phone_not_price():
    parsed = get_ai_service()._parse_regex("2к 450000 +998901112233")
    assert parsed["price"] == 450000.0  # телефон не спутан с ценой


def test_parse_listing_offline_spaced_price_and_phone():
    svc = get_ai_service()
    parsed = svc._listing_offline(
        "Продаётся 3-комн. квартира, центр, 5/9, 78 м2. Цена 65 000 000 сум. Тел: +998901234567"
    )
    assert parsed["phone"] == "+998901234567"      # цена не принята за телефон
    assert parsed["price"] == 65000000.0           # разряды через пробел учтены
    assert parsed["rooms"] == 3
    assert parsed["deal_type"] == "sale"
    assert parsed["description"]                    # описание есть
    assert "+998901234567" not in parsed["description"]  # телефон убран из описания


def test_find_matches_budget_and_filters():
    svc = get_ai_service()
    client = {"deal_type": "rent", "budget": 2500000, "district": "центр", "rooms": 2}
    props = [
        {"type": "rent", "district": "Центр", "rooms": 2, "price": 2400000, "id": "A1"},
        {"type": "rent", "district": "Центр", "rooms": 2, "price": 5000000, "id": "A2"},  # > +10%
        {"type": "sale", "district": "Центр", "rooms": 2, "price": 100, "id": "A3"},       # др. сделка
    ]
    ids = [p["id"] for p in svc.find_matches(client, props)]
    assert ids == ["A1"]


def test_normalize_listing_caps_phone_and_currency():
    """Несколько телефонов → один, ≤32 симв.; валюта $ распознаётся; длинные строки режутся."""
    from bot.services.ai_service import AIService

    data = {
        "deal_type": "sale",
        "phone": "+998885199595 +998942101222 +998931112070",  # три номера
        "currency": "570 млн сўм (= 47,500$)",
        "district": "Я" * 300,   # заведомо длиннее 128
        "price": "570000000",
    }
    out = AIService._normalize_listing(data)
    assert len(out["owner_phone" if "owner_phone" in out else "phone"]) <= 32
    assert out["phone"].startswith("+998885199595")
    assert out["currency"] == "$"
    assert len(out["district"]) <= 128
    assert out["price"] == 570000000.0
