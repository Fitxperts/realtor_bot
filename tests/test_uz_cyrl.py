"""Узбекская кириллица: полнота, актуальность (хеш латиницы), сохранность разметки, сквозной сценарий."""
from __future__ import annotations

import re

from sqlalchemy import select

from bot import i18n, i18n_uz_cyrl
from bot.database.models import BotUser
from bot.platform import texts as platform_texts
from bot.tenancy import agency_scope

USER = 890
PROTECTED = re.compile(r"<[^>]+>|\{[^{}]*\}|https?://\S+|tg://\S+|@\w+|(?<![\w/])/[a-z_]+"
                       r"|\b[A-Z]{2,}|\b(?:Excel|Payme|Click|WhatsApp|YouTube)\b")
SECTIONS = ((i18n.BUTTONS, i18n_uz_cyrl.BUTTONS), (i18n.MESSAGES, i18n_uz_cyrl.MESSAGES),
            (platform_texts.TEXTS, i18n_uz_cyrl.PLATFORM))


def test_generated_for_every_text_and_up_to_date():
    stale = [key for table, cyrl in SECTIONS for key, v in table.items()
             if "uz" in v and not (key in cyrl and i18n.source_ok(cyrl[key], v["uz"]))]
    assert not stale, f"латиница изменилась — перезапустите scripts/gen_uz_cyrl.py: {stale[:10]}"


def test_markup_preserved_and_no_latin_left():
    for table, cyrl in SECTIONS:
        for key, (_, text) in cyrl.items():
            uz = table[key]["uz"]
            assert PROTECTED.findall(uz) == PROTECTED.findall(text), key      # теги, {…}, /команды, @имена
            assert uz.count("\n") == text.count("\n"), key
            assert not re.search(r"[A-Za-z]", PROTECTED.sub("", text)), f"латиница осталась: {key}: {text}"


def test_t_uses_cyrillic():
    assert i18n.btn("catalog", "uz_cyrl") == "📋 Эълонлар"
    assert i18n.t("ask_district", "uz_cyrl").startswith("📍 Қайси тумандан")
    assert "7" in i18n.t("media_accepted", "uz_cyrl", n=7)
    assert platform_texts.t("btn_connect", "uz_cyrl") == "🚀 Агентликни улаш"


async def test_user_picks_cyrillic_and_menu_works(make_harness):
    h = await make_harness()
    await h.message(USER, "/start")
    await h.callback(USER, "lang:uz_cyrl")
    async with h.maker() as session:
        with agency_scope(1):
            user = (await session.scalars(select(BotUser).where(BotUser.telegram_id == USER))).one()
    assert user.language == "uz_cyrl"
    await h.callback(USER, "role:client")
    assert any("Уй" in t or "эълон" in t.lower() for t in h.texts_to(USER)[-2:])
    h.clear()
    await h.message(USER, "📋 Эълонлар")                                # кнопка меню на кириллице
    assert h.sent(USER), "кнопка «Эълонлар» не сработала"
    assert not any(re.search(r"[a-z]{4,}", re.sub(r"<[^>]+>|@\w+|/\w+", "", t)) for t in h.texts_to(USER))
