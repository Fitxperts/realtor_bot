"""Вебхуки ботов агентств: /webhook/a<id> со своим секретом у каждого.

Агентство №1 остаётся на старом пути (/webhook) — его вебхук не меняется.
"""
from __future__ import annotations

import secrets

from aiogram import Bot
from aiogram.webhook.aiohttp_server import BaseRequestHandler
from aiohttp import web

from bot import bots
from bot.tenancy import DEFAULT_AGENCY_ID


def agency_path(base_path: str, agency_id: int) -> str:
    return base_path if agency_id == DEFAULT_AGENCY_ID else f"{base_path.rstrip('/')}/a{agency_id}"


class AgencyRequestHandler(BaseRequestHandler):
    """Один обработчик на все боты агентств: бот — по номеру агентства в пути."""

    async def resolve_bot(self, request: web.Request) -> Bot:
        raw = request.match_info.get("agency_id", "")
        agency_id = int(raw) if raw.isdigit() else 0
        bot = bots.bot_for(agency_id) if agency_id != DEFAULT_AGENCY_ID else None
        if bot is None:
            raise web.HTTPNotFound()
        return bot

    def verify_secret(self, telegram_secret_token: str, bot: Bot) -> bool:
        expected = bots.secret_for(bots.agency_for_bot(bot.id))
        return bool(expected) and secrets.compare_digest(telegram_secret_token, expected)

    async def close(self) -> None:
        for agency_id, bot in bots.agency_bots().items():
            if agency_id != DEFAULT_AGENCY_ID:
                await bot.session.close()

    def register_routes(self, app: web.Application, base_path: str) -> None:
        self.register(app, path=f"{base_path.rstrip('/')}/a{{agency_id:\\d+}}")
