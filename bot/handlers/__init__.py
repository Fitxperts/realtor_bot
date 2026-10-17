"""Регистрация всех роутеров хендлеров.

Порядок: admin (ограничен фильтром IsAdmin) → common (определение роли, меню)
→ owner → client. common держит /start и первое сообщение; специфичные шаги
сценариев обрабатываются в owner/client.
"""
from aiogram import Dispatcher

from bot.config import get_settings
from bot.handlers import admin, catalog, client, common, demo, lead, owner


def register_routers(dp: Dispatcher) -> None:
    dp.include_router(admin.router)
    dp.include_router(lead.router)      # состояния LeadForm (заявка с канала/каталога)
    if get_settings().demo_mode:
        dp.include_router(demo.router)  # демо-бот: «Хочу такой бот» (до common — ловит кнопку меню)
    dp.include_router(catalog.router)   # просмотр объявлений в боте (до common — ловит кнопку меню)
    dp.include_router(common.router)
    dp.include_router(owner.router)
    dp.include_router(client.router)
