"""Middleware: определяет язык пользователя и прокидывает ``lang`` в хендлеры.

Работает поверх DbSessionMiddleware (сессия уже в ``data['session']``).
Читает язык из bot_users; если пользователя ещё нет или язык не выбран —
подставляет язык по умолчанию. Регистрируется на message и callback_query,
где доступен ``event.from_user``.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.database import crud
from bot.i18n import DEFAULT_LANG, normalize


class LanguageMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        lang = DEFAULT_LANG
        user = getattr(event, "from_user", None)
        session = data.get("session")
        if user is not None and session is not None:
            row = await crud.get_user(session, user.id)
            if row is not None and row.language:
                lang = normalize(row.language)
        data["lang"] = lang
        return await handler(event, data)
