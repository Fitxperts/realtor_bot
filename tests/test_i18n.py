"""Тесты мультиязычности: полнота переводов и подстановка параметров."""
from bot import i18n


def test_all_messages_cover_three_languages():
    for key, variants in i18n.MESSAGES.items():
        assert set(variants) >= set(i18n.LANGUAGES), f"неполный перевод: {key}"
        for lang in i18n.LANGUAGES:
            assert variants[lang].strip(), f"пустой перевод {key}/{lang}"


def test_all_buttons_cover_three_languages():
    for action, variants in i18n.BUTTONS.items():
        assert set(variants) >= set(i18n.LANGUAGES), f"неполная кнопка: {action}"


def test_t_fallback_and_format():
    # неизвестный ключ -> сам ключ
    assert i18n.t("no_such_key", "ru") == "no_such_key"
    # неизвестный язык -> русский по умолчанию
    assert i18n.t("cancel_none", "xx") == i18n.MESSAGES["cancel_none"]["ru"]
    # подстановка параметров
    assert "7" in i18n.t("media_accepted", "uz", n=7)


def test_menu_texts_are_unique_per_language():
    # 6 кнопок меню × 3 языка = 18 уникальных подписей (для F.text.in_)
    assert len(i18n.all_menu_texts()) == 18


def test_normalize():
    assert i18n.normalize("uz") == "uz"
    assert i18n.normalize(None) == i18n.DEFAULT_LANG
    assert i18n.normalize("fr") == i18n.DEFAULT_LANG
