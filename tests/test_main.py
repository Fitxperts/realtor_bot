"""Тесты точки входа: общая сборка и выбор режима polling/webhook."""
from aiogram import Bot, Dispatcher

from bot import main
from bot.config import get_settings


def test_build_returns_bot_dispatcher_with_routers():
    bot, dp, storage = main._build(get_settings())
    try:
        assert isinstance(bot, Bot)
        assert isinstance(dp, Dispatcher)
        assert dp.sub_routers, "роутеры должны быть зарегистрированы"
    finally:
        # сеть не трогалась; роутеры отцепляем — их подключают и сквозные тесты (tests/flow.py)
        for router in dp.sub_routers:
            router._parent_router = None


def test_default_mode_is_polling():
    # без WEBHOOK_URL (conftest его не задаёт) — режим polling
    assert get_settings().webhook_url == ""


def test_webhook_and_polling_runners_exist():
    assert callable(main._run_polling)
    assert callable(main._run_webhook)
