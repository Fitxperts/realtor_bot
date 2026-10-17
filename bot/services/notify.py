"""Служебные уведомления: агентству (его админам) и платформе (владельцу SaaS).

- notify_admins   — админам ТЕКУЩЕГО агентства: заявки, модерация, отчёты;
- notify_platform — владельцу платформы (ADMIN_TELEGRAM_ID в .env): сбой ИИ,
  бэкапы, ошибки кода. Агентству такие технические детали не нужны.
"""
from __future__ import annotations

import logging

from aiogram import Bot

from bot.agency_config import cfg
from bot.config import get_settings

logger = logging.getLogger(__name__)


async def _send_all(bot: Bot, chat_ids: list[int], text: str, **kwargs) -> None:
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text, **kwargs)
        except Exception:  # noqa: BLE001 - админ мог заблокировать бота
            logger.debug("Не удалось уведомить %s", chat_id)


async def notify_admins(bot: Bot, text: str, **kwargs) -> None:
    await _send_all(bot, cfg().admin_ids, text, **kwargs)


async def notify_platform(bot: Bot, text: str) -> None:
    await _send_all(bot, list(get_settings().admin_ids), text)
