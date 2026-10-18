"""Бот платформы: агентство подключается само → его бот сразу работает; супер-админка."""
from __future__ import annotations

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import DeleteMessage, EditMessageText
from aiogram.types import ChatMemberAdministrator, ChatMemberMember, User
from cryptography.fernet import Fernet
from sqlalchemy import select

from bot import agency_config, bots, crypto
from bot.database.models import Agency
from bot.platform import onboarding
from bot.platform.runtime import build_platform
from tests.flow import FakeSession, Harness, buttons

OWNER = 950            # хозяин нового агентства
PLATFORM = 1           # владелец платформы (ADMIN_TELEGRAM_ID в conftest)
NEW_TOKEN = "7777777:AAHnewAgencyTokenXXXXXXXXXXXXXXXXXXXX"


def _admin_member(user_id: int, can_post: bool = True) -> ChatMemberAdministrator:
    flags = dict.fromkeys(("can_be_edited", "is_anonymous", "can_manage_chat", "can_delete_messages",
                           "can_manage_video_chats", "can_restrict_members", "can_promote_members",
                           "can_change_info", "can_invite_users", "can_post_stories", "can_edit_stories",
                           "can_delete_stories"), False)
    return ChatMemberAdministrator(user=User(id=user_id, is_bot=True, first_name="b"), can_post_messages=can_post,
                                   **flags)


class _Platform:
    """Бот платформы поверх стенда Harness: те же БД и реестры."""

    def __init__(self, h: Harness, monkeypatch, *, member=None) -> None:
        self.h = h
        self.api = FakeSession()
        self.bot, self.dp = build_platform("6000000:PLATFORMxTOKENxxxxxxxxxxxxxxxxxxxxxxx", MemoryStorage())
        self.bot.session = self.api
        self.agency_apis: list[FakeSession] = []
        me = User(id=7777777, is_bot=True, first_name="New", username="new_agency_bot")
        member = member if member is not None else _admin_member(7777777)

        def factory(token):                      # «Telegram» для временного и боевого бота агентства
            api = FakeSession(me=me, member=member)
            self.agency_apis.append(api)
            return Bot(token, session=api, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

        monkeypatch.setattr(onboarding, "bot_factory", factory)
        monkeypatch.setattr(bots, "make_bot", factory)
        monkeypatch.setenv("PLATFORM_SECRET_KEY", Fernet.generate_key().decode())

    def detach(self):
        for router in self.dp.sub_routers:
            router._parent_router = None

    async def msg(self, uid, text):
        import datetime as dt

        from aiogram.types import Chat, Message, Update
        m = Message(message_id=len(self.api.calls) + 1, date=dt.datetime.now(), chat=Chat(id=uid, type="private"),
                    from_user=Harness.user(uid), text=text)
        await self.dp.feed_update(self.bot, Update(update_id=len(self.api.calls) + 1, message=m))

    async def cb(self, uid, data):
        import datetime as dt

        from aiogram.types import CallbackQuery, Chat, Message, Update
        origin = Message(message_id=1, date=dt.datetime.now(), chat=Chat(id=uid, type="private"), text="…")
        await self.dp.feed_update(self.bot, Update(update_id=len(self.api.calls) + 1, callback_query=CallbackQuery(
            id="1", from_user=Harness.user(uid), chat_instance="c", data=data, message=origin)))

    def texts(self, uid):
        return [getattr(c, "text", "") or "" for c in self.api.calls if getattr(c, "chat_id", None) == uid]


@pytest.fixture
async def platform(make_harness, monkeypatch):
    h = await make_harness()
    p = _Platform(h, monkeypatch)
    yield p
    p.detach()


async def _onboard(p: _Platform, channel="@new_channel"):
    await p.msg(OWNER, "/start")
    await p.cb(OWNER, "pl:lang:ru")
    await p.cb(OWNER, "pl:connect")
    await p.msg(OWNER, "Namangan Uylari")
    await p.cb(OWNER, "pl:city:3")                     # Наманган
    await p.msg(OWNER, NEW_TOKEN)
    await p.msg(OWNER, channel)


async def test_full_onboarding_then_new_bot_works(platform):
    p = platform
    await _onboard(p)
    assert any(isinstance(c, DeleteMessage) for c in p.api.calls)          # токен удалён из чата
    assert any("Бот @new_agency_bot найден" in t for t in p.texts(OWNER))
    await p.msg(OWNER, "+998 90 123 45 67 @namangan_agent")
    assert "Namangan Uylari · Наманган" in p.texts(OWNER)[-1]
    await p.cb(OWNER, "pl:launch")
    assert any("Готово! @new_agency_bot работает" in t for t in p.texts(OWNER))
    assert any("Новое агентство" in t for t in p.texts(PLATFORM))

    async with p.h.maker() as session:
        row = (await session.scalars(select(Agency).where(Agency.bot_id == 7777777))).one()
        assert NEW_TOKEN not in row.bot_token_enc and crypto.decrypt(row.bot_token_enc) == NEW_TOKEN
        assert row.admin_ids == str(OWNER) and row.channel_id == "@new_channel"
        assert row.contacts == "+998901234567:namangan_agent"
    config = agency_config.cfg(row.id)
    assert config.admin_ids == [OWNER] and config.name == "Namangan Uylari"

    # бот агентства уже в реестре: хозяин открывает его — он администратор
    new_bot = bots.bot_for(row.id)
    assert new_bot is not None and new_bot.id == 7777777
    await p.h.message(OWNER, "📊 Статистика", bot=new_bot)
    live_api = p.agency_apis[-1]
    assert any("Статистика" in (getattr(c, "text", "") or "") for c in live_api.calls)


async def test_bad_token_and_channel_without_rights(make_harness, monkeypatch):
    h = await make_harness()
    p = _Platform(h, monkeypatch, member=ChatMemberMember(user=User(id=7777777, is_bot=True, first_name="b")))
    try:
        await p.msg(OWNER, "/start")
        await p.cb(OWNER, "pl:lang:ru")
        await p.cb(OWNER, "pl:connect")
        await p.msg(OWNER, "Agency")
        await p.cb(OWNER, "pl:city:0")
        await p.msg(OWNER, "не токен")
        assert "Не похоже на токен" in p.texts(OWNER)[-1]
        await p.msg(OWNER, NEW_TOKEN)
        await p.msg(OWNER, "@channel_x")                                   # бот там обычный участник
        assert "не администратор" in p.texts(OWNER)[-1]
    finally:
        p.detach()


async def test_same_bot_cannot_be_connected_twice(platform):
    async with platform.h.maker() as session:
        session.add(Agency(id=5, name="Уже есть", bot_id=7777777))
        await session.commit()
    await platform.msg(OWNER, "/start")
    await platform.cb(OWNER, "pl:lang:ru")
    await platform.cb(OWNER, "pl:connect")
    await platform.msg(OWNER, "X")
    await platform.cb(OWNER, "pl:city:0")
    await platform.msg(OWNER, NEW_TOKEN)
    assert "уже подключён" in platform.texts(OWNER)[-1]


async def test_owner_limit(platform):
    async with platform.h.maker() as session:
        session.add_all([Agency(id=10 + i, name=f"A{i}", owner_telegram_id=OWNER) for i in range(3)])
        await session.commit()
    await platform.msg(OWNER, "/start")
    await platform.cb(OWNER, "pl:lang:ru")
    await platform.cb(OWNER, "pl:connect")
    assert "уже подключили 3" in platform.texts(OWNER)[-1]


async def test_superadmin_list_and_disable(platform):
    p = platform
    await _onboard(p)
    await p.cb(OWNER, "pl:skipcontacts")
    await p.cb(OWNER, "pl:launch")
    async with p.h.maker() as session:
        agency_id = (await session.scalars(select(Agency.id).where(Agency.bot_id == 7777777))).one()

    await p.msg(OWNER, "/agencies")                                     # не владелец платформы — тишина
    assert not any("Агентства:" in t for t in p.texts(OWNER))
    await p.msg(PLATFORM, "/agencies")
    assert "Namangan Uylari" in p.texts(PLATFORM)[-1]
    await p.msg(PLATFORM, f"/agency {agency_id}")
    assert buttons(p.api.calls[-1]) == [("⏸ Отключить", f"plat:off:{agency_id}")]
    await p.cb(PLATFORM, f"plat:off:{agency_id}")
    assert any(isinstance(c, EditMessageText) and "отключено" in c.text for c in p.api.calls)
    assert agency_config.cfg(agency_id).active is False

    live_api = p.agency_apis[-1]
    before = len(live_api.calls)
    await p.h.message(OWNER, "/start", bot=bots.bot_for(agency_id))     # отключённый бот молчит
    assert len(live_api.calls) == before


def test_parse_contacts():
    assert onboarding.parse_contacts("+998 90 111-22-33 @agent_one\n93 222 33 44") == \
        "+998901112233:agent_one,932223344:"
