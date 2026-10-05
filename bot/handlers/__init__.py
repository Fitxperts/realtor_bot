"""Регистрация всех роутеров хендлеров.

Порядок: admin (ограничен фильтром IsAdmin) → common (определение роли, меню)
→ owner → client. common держит /start и первое сообщение; специфичные шаги
сценариев обрабатываются в owner/client.
"""
from aiogram import Dispatcher

from bot.handlers import admin, catalog, client, common, lead, owner


def register_routers(dp: Dispatcher) -> None:
    dp.include_router(admin.router)
    dp.include_router(lead.router)      # состояния LeadForm (заявка с канала/каталога)
    dp.include_router(catalog.router)   # просмотр объявлений в боте (до common — ловит кнопку меню)
    dp.include_router(common.router)
    dp.include_router(owner.router)
    dp.include_router(client.router)
