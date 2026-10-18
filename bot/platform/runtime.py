"""Сборка бота платформы: свой диспетчер (онбординг + супер-админка), общая БД и Redis."""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import BaseStorage

from bot import bots
from bot.middlewares.db import DbSessionMiddleware
from bot.platform import onboarding, superadmin

PLATFORM_PATH_SUFFIX = "/platform"


def build_platform(token: str, storage: BaseStorage) -> tuple[Bot, Dispatcher]:
    bot = bots.make_bot(token)
    dp = Dispatcher(storage=storage)
    dp.update.middleware(DbSessionMiddleware())
    dp.include_router(superadmin.router)   # команды владельца — раньше онбординга
    dp.include_router(onboarding.router)
    return bot, dp
