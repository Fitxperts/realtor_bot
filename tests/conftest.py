"""Общие фикстуры тестов: изолированная in-memory БД и заглушки окружения."""
from __future__ import annotations

import os

# Переменные окружения нужны до импорта пакета bot (config читает их)
os.environ.setdefault("BOT_TOKEN", "123456789:TESTtokenABCDEFGHIJKLMNOPQRSTUVWXYZ12")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "1")
os.environ.setdefault("CHANNEL_ID", "@test")

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from bot.database.models import Base  # noqa: E402


@pytest_asyncio.fixture
async def session():
    """Асинхронная сессия к чистой in-memory SQLite (StaticPool — единое соединение)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def make_harness(monkeypatch, tmp_path):
    """Фабрика сквозного стенда (tests/flow.py): настоящий Dispatcher + поддельный Telegram."""
    from tests.flow import Harness

    created: list = []

    async def factory(**settings):
        harness = await Harness.create(monkeypatch, tmp_path, **settings)
        created.append(harness)
        return harness

    yield factory
    for harness in created:
        harness.detach()


class FakeBot:
    """Минимальный бот-заглушка для тестов публикации/уведомлений."""

    def __init__(self) -> None:
        self.calls: list = []
        self.messages: list = []
        self.markups: list = []
        self._id = 100

    async def me(self):
        return _Me("test_bot")

    async def send_message(self, chat, text, parse_mode=None, reply_markup=None):
        self.calls.append(("message", chat, len(text)))
        self.messages.append((chat, text))
        self.markups.append(reply_markup)
        self._id += 1
        return _Msg(self._id)

    async def send_photo(self, chat, photo, caption=None, parse_mode=None, reply_markup=None):
        self.calls.append(("photo", chat, caption is not None))
        self.messages.append((chat, caption))
        self.markups.append(reply_markup)
        self._id += 1
        return _Msg(self._id)

    async def send_media_group(self, chat, media):
        self.calls.append(("album", chat, len(media)))
        self._id += 1
        return [_Msg(self._id)]

    async def edit_message_caption(self, chat_id, message_id, caption, parse_mode=None):
        self.calls.append(("edit_caption", message_id))

    async def edit_message_text(self, text, chat_id, message_id, parse_mode=None):
        self.calls.append(("edit_text", message_id))

    async def delete_message(self, chat_id, message_id):
        self.calls.append(("delete", message_id))


class _Msg:
    def __init__(self, mid: int) -> None:
        self.message_id = mid


class _Me:
    def __init__(self, username: str) -> None:
        self.username = username
