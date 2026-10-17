"""Настройки агентства: №1 из .env, остальные из БД; бренд, канал, админы, уведомления."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot import agency_config
from bot.agency_config import cfg, from_row, load_agencies, register
from bot.database.models import Agency
from bot.handlers.admin import IsAdmin
from bot.services.ai_service import get_ai_service
from bot.services.notify import notify_admins, notify_platform
from bot.tenancy import agency_scope
from tests.conftest import FakeBot


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(agency_config._registry)
    agency_config._registry.clear()
    yield
    agency_config._registry.clear()
    agency_config._registry.update(saved)


def _second() -> Agency:
    return Agency(id=2, name="Toshkent Uylari", status="active", bot_username="tosh_uy_bot",
                  channel_id="@tosh_uylari", contacts="+998901110000:tosh_admin", watermark="Toshkent Uylari",
                  admin_ids="200,201", language="uz")


def test_agency_one_comes_from_env():
    config = cfg()
    assert config.id == 1 and config.channel_id == "@test" and config.admin_ids == [1]


def test_other_agency_from_db_row():
    register(from_row(_second()))
    with agency_scope(2):
        config = cfg()
    assert config.channel_id == "@tosh_uylari" and config.channel_url == "https://t.me/tosh_uylari"
    assert config.publish_url == "https://t.me/tosh_uy_bot?start=add"
    assert config.admin_ids == [200, 201] and config.contacts == [("+998901110000", "tosh_admin")]
    assert cfg().channel_id == "@test"               # вне контекста — снова агентство №1


def test_post_uses_agency_brand():
    register(from_row(_second()))
    data = {"deal_type": "rent", "property_kind": "apartment", "price": 300, "currency": "$"}
    with agency_scope(2):
        post = get_ai_service().generate_description(data)
    assert "Toshkent Uylari" in post and "tosh\\_admin" in post and "t.me/tosh_uylari" in post
    own = get_ai_service().generate_description(data)           # агентство №1 — свои контакты из .env
    assert "Aziko\\_3133" in own and "tosh" not in own


async def test_notifications_go_to_right_people():
    register(from_row(_second()))
    bot = FakeBot()
    with agency_scope(2):
        await notify_admins(bot, "заявка агентства 2")
        await notify_platform(bot, "сбой ИИ")
    assert bot.messages == [(200, "заявка агентства 2"), (201, "заявка агентства 2"), (1, "сбой ИИ")]


async def test_admin_of_one_agency_is_not_admin_of_another():
    register(from_row(_second()))
    event = SimpleNamespace(from_user=SimpleNamespace(id=1))
    assert await IsAdmin()(event) is True
    with agency_scope(2):
        assert await IsAdmin()(event) is False
        assert await IsAdmin()(SimpleNamespace(from_user=SimpleNamespace(id=200))) is True


async def test_load_agencies_and_unknown_agency(session):
    session.add_all([Agency(id=1, name="Агентство №1", status="active"), _second(),
                     Agency(id=3, name="Отключённое", status="disabled")])
    await session.commit()
    configs = await load_agencies(session)
    assert [c.id for c in configs] == [1, 2, 3]
    assert cfg(3).active is False and cfg(2).active is True
    assert cfg(99).active is False and cfg(99).admin_ids == []   # неизвестное — пустое и неактивное
