"""Создать базу из DATABASE_URL, если её ещё нет.

Нужно демо-боту (и в будущем — агентствам): он живёт на том же Postgres, но в
своей базе realtor_demo, которую образ postgres сам не создаёт.

    python -m bot.ensure_db
"""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)


async def ensure_database(url: str) -> bool:
    """True — база создана, False — уже была (или это не Postgres)."""
    import asyncpg

    u = make_url(url)
    if not u.drivername.startswith("postgresql"):
        return False
    name = u.database or ""
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"Недопустимое имя базы: {name!r}")
    conn = await asyncpg.connect(
        host=u.host, port=u.port or 5432, user=u.username, password=u.password, database="postgres"
    )
    try:
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            return False
        await conn.execute(f'CREATE DATABASE "{name}"')
        return True
    finally:
        await conn.close()


if __name__ == "__main__":
    from bot.config import get_settings
    from bot.logging_setup import setup_logging

    setup_logging()
    created = asyncio.run(ensure_database(get_settings().database_url))
    logger.info("База %s", "создана" if created else "уже есть")
