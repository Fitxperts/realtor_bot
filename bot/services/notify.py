"""Служебные уведомления всем админам (отчёты, тревоги)."""
from __future__ import annotations

import logging

from aiogram import Bot

from bot.config import get_settings

logger = logging.getLogger(__name__)


async def notify_admins(bot: Bot, text: str) -> None:
    for admin_id in get_settings().admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001 - админ мог заблокировать бота
            logger.debug("Не удалось уведомить админа %s", admin_id)
