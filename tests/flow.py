"""Сквозные тесты сценариев: настоящий Dispatcher со всеми роутерами и middleware,
поддельный Telegram API (записывает вызовы) и база SQLite в памяти.

    harness = await Harness.create(monkeypatch, demo_mode=True)
    await harness.message(user_id, "/start")
    await harness.callback(user_id, "lang:ru")
    harness.texts_to(user_id)   # что бот написал пользователю
"""
from __future__ import annotations

import datetime as dt
import itertools

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    GetChatMember,
    GetMe,
    SendDocument,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.config import get_settings
from bot.database import session as db_session
from bot.database.models import Base

BOT_USER = User(id=4242, is_bot=True, first_name="Demo", username="demo_test_bot")


class FakeSession(BaseSession):
    """Вместо HTTP к Telegram — запись вызова и правдоподобный ответ."""

    def __init__(self, me: User | None = None, member=None) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._ids = itertools.count(1000)
        self.me = me or BOT_USER
        self.member = member          # ответ на getChatMember (None → ошибка «нет в канале»)

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return self.me
        if isinstance(method, GetChatMember):
            if self.member is None:
                raise RuntimeError("Bad Request: chat not found")
            return self.member
        if isinstance(method, SendMediaGroup):
            return [self._msg(method.chat_id) for _ in method.media]
        if isinstance(method, SendMessage | SendPhoto | SendDocument):
            return self._msg(method.chat_id, getattr(method, "text", None))
        return True  # answerCallbackQuery, editMessageReplyMarkup, deleteMessage, setMyCommands…

    def _msg(self, chat_id, text=None) -> Message:
        chat = Chat(id=chat_id if isinstance(chat_id, int) else -100, type="private")
        return Message(message_id=next(self._ids), date=dt.datetime.now(), chat=chat, from_user=BOT_USER,
                       text=text)

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):  # скачивание файлов в этих тестах не нужно
        raise NotImplementedError
        yield b""  # pragma: no cover


class Harness:
    def __init__(self, bot: Bot, dp: Dispatcher, api: FakeSession, maker) -> None:
        self.bot, self.dp, self.api, self.maker = bot, dp, api, maker
        self._update_ids = itertools.count(1)
        self._msg_ids = itertools.count(1)

    @classmethod
    async def create(cls, monkeypatch, tmp_path, **settings) -> Harness:
        engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False},
                                     poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(db_session, "_sessionmaker", maker)
        async with maker() as session:          # как миграция 0006: агентство №1 есть всегда
            from bot.database.models import Agency
            session.add(Agency(id=1, name="Агентство №1", status="active"))
            await session.commit()

        cfg = get_settings()
        defaults = {"media_dir": str(tmp_path / "media"), "demo_photos_dir": str(tmp_path / "no_photos"),
                    "channel_id": "@demo_channel"}
        for key, value in {**defaults, **settings}.items():
            monkeypatch.setattr(cfg, key, value)

        from bot.services import demo
        monkeypatch.setattr(demo, "PUBLISH_PAUSE", 0)  # без пауз между постами в канал

        # ИИ выключен: разбор регулярками, без сетевых вызовов
        from bot.services.ai_service import get_ai_service
        from bot.services.llm_providers import LLMRouter
        monkeypatch.setattr(get_ai_service(), "router", LLMRouter([]))

        from bot import agency_config, bots
        from bot.bots import AgencyMiddleware
        from bot.handlers import register_routers
        from bot.middlewares.db import DbSessionMiddleware
        from bot.middlewares.i18n import LanguageMiddleware

        saved_configs = dict(agency_config._registry)
        api = FakeSession()
        bot = Bot("42:TEST", session=api, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        bots.clear()
        bots.register_bot(1, bot)                       # основной бот = агентство №1, как в проде
        dp = Dispatcher(storage=MemoryStorage())
        dp.update.outer_middleware(AgencyMiddleware())
        dp.update.middleware(DbSessionMiddleware())
        dp.message.middleware(LanguageMiddleware())
        dp.callback_query.middleware(LanguageMiddleware())
        register_routers(dp)
        harness = cls(bot, dp, api, maker)
        harness._saved_configs = saved_configs
        return harness

    def add_agency_bot(self, agency_id: int, token: str, **agency_fields) -> tuple[Bot, FakeSession]:
        """Ещё одно агентство со своим ботом и своим «Telegram» (для тестов white-label)."""
        from bot import bots
        from bot.agency_config import from_row, register
        from bot.database.models import Agency

        api = FakeSession()
        bot = Bot(token, session=api, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        bots.register_bot(agency_id, bot, agency_fields.pop("webhook_secret", None))
        agency_fields.setdefault("name", f"Агентство {agency_id}")
        agency_fields.setdefault("status", "active")
        register(from_row(Agency(id=agency_id, **agency_fields)))
        return bot, api

    def detach(self) -> None:
        """Роутеры aiogram цепляются к одному диспетчеру — отцепляем для следующего теста.
        Заодно чистим реестры ботов и агентств."""
        from bot import agency_config, bots

        for router in self.dp.sub_routers:
            router._parent_router = None
        self.dp.sub_routers.clear()
        bots.clear()
        agency_config._registry.clear()
        agency_config._registry.update(getattr(self, "_saved_configs", {}))

    @staticmethod
    def user(uid: int) -> User:
        return User(id=uid, is_bot=False, first_name=f"User{uid}", username=f"user{uid}")

    async def _feed(self, bot: Bot | None = None, **kwargs) -> None:
        await self.dp.feed_update(bot or self.bot, Update(update_id=next(self._update_ids), **kwargs))

    async def message(self, uid: int, text: str | None = None, *, contact: str | None = None,
                      bot: Bot | None = None) -> None:
        msg = Message(
            message_id=next(self._msg_ids), date=dt.datetime.now(), chat=Chat(id=uid, type="private"),
            from_user=self.user(uid), text=text,
            contact=Contact(phone_number=contact, first_name="U", user_id=uid) if contact else None,
        )
        await self._feed(bot, message=msg)

    async def callback(self, uid: int, data: str, *, bot: Bot | None = None) -> None:
        origin = Message(message_id=next(self._msg_ids), date=dt.datetime.now(),
                         chat=Chat(id=uid, type="private"), from_user=BOT_USER, text="…")
        await self._feed(bot, callback_query=CallbackQuery(
            id=str(next(self._update_ids)), from_user=self.user(uid), chat_instance="ci", data=data,
            message=origin,
        ))

    # --- что «ушло в Telegram» ---------------------------------------------
    def sent(self, chat_id=None) -> list[TelegramMethod]:
        return [c for c in self.api.calls if isinstance(c, SendMessage | SendPhoto | SendDocument)
                and (chat_id is None or c.chat_id == chat_id)]

    def texts_to(self, chat_id) -> list[str]:
        return [getattr(c, "text", None) or getattr(c, "caption", None) or "" for c in self.sent(chat_id)]

    def last_to(self, chat_id) -> TelegramMethod:
        return self.sent(chat_id)[-1]

    def clear(self) -> None:
        self.api.calls.clear()


def buttons(method) -> list[tuple[str, str | None]]:
    """(текст, callback_data) всех кнопок сообщения — inline и reply."""
    markup = getattr(method, "reply_markup", None)
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None) or []
    return [(b.text, getattr(b, "callback_data", None)) for row in rows for b in row]
