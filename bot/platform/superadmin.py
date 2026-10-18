"""Супер-админка владельца платформы (в боте платформы): агентства, их состояние, вкл/выкл."""
from __future__ import annotations

import datetime as dt
from html import escape

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.agency_config import from_row, register
from bot.config import get_settings
from bot.database.models import Agency, Client, Event, Property, PropertyStatus
from bot.tenancy import ALL_AGENCIES
from bot.utils import timeutils


class IsPlatformOwner(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return bool(event.from_user and get_settings().is_admin(event.from_user.id))


router = Router(name="platform_superadmin")
router.message.filter(IsPlatformOwner())
router.callback_query.filter(IsPlatformOwner())


async def _per_agency(session: AsyncSession, stmt) -> dict[int, int]:
    return dict((await session.execute(stmt.execution_options(**ALL_AGENCIES))).all())


async def agency_stats(session: AsyncSession) -> dict[str, dict[int, int]]:
    week = timeutils.now() - dt.timedelta(days=7)
    return {
        "active": await _per_agency(session, select(Property.agency_id, func.count())
                                    .where(Property.status == PropertyStatus.active).group_by(Property.agency_id)),
        "clients": await _per_agency(session, select(Client.agency_id, func.count()).group_by(Client.agency_id)),
        "leads7": await _per_agency(session, select(Event.agency_id, func.count())
                                    .where(Event.type == "lead", Event.created_at >= week).group_by(Event.agency_id)),
    }


@router.message(Command("agencies"))
async def list_agencies(message: Message, session: AsyncSession) -> None:
    rows = list(await session.scalars(select(Agency).order_by(Agency.id)))
    stats = await agency_stats(session)
    lines = [f"🏢 <b>Агентства: {len(rows)}</b>", ""]
    for r in rows:
        mark = "⏸" if r.status == "disabled" else "✅"
        lines.append(f"{mark} #{r.id} {escape(r.name)} · {escape(r.city or '—')} · @{r.bot_username or '—'}\n"
                     f"     объектов {stats['active'].get(r.id, 0)} · клиентов {stats['clients'].get(r.id, 0)} · "
                     f"заявок за 7 дн. {stats['leads7'].get(r.id, 0)}")
    lines.append("\nПодробнее: /agency &lt;номер&gt;")
    await message.answer("\n".join(lines))


def _toggle_kb(row: Agency) -> InlineKeyboardMarkup:
    if row.status == "disabled":
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="▶️ Включить",
                                                                           callback_data=f"plat:on:{row.id}")]])
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏸ Отключить",
                                                                       callback_data=f"plat:off:{row.id}")]])


def _card(row: Agency) -> str:
    return (f"🏢 <b>#{row.id} {escape(row.name)}</b> · {escape(row.city or '—')}\n"
            f"Статус: {'⏸ отключено' if row.status == 'disabled' else '✅ работает'}\n"
            f"🤖 @{row.bot_username or '—'} · 📢 {escape(row.channel_id or '—')}\n"
            f"👤 владелец <code>{row.owner_telegram_id or '—'}</code> · админы {escape(row.admin_ids or '—')}\n"
            f"📅 с {row.created_at:%d.%m.%Y}")


@router.message(Command("agency"))
async def agency_card(message: Message, command: CommandObject, session: AsyncSession) -> None:
    arg = (command.args or "").strip()
    row = await session.get(Agency, int(arg)) if arg.isdigit() else None
    if row is None:
        await message.answer("Использование: /agency &lt;номер&gt; (список — /agencies)")
        return
    await message.answer(_card(row), reply_markup=_toggle_kb(row))


@router.callback_query(F.data.startswith("plat:"))
async def toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    _, action, raw = callback.data.split(":")
    row = await session.get(Agency, int(raw))
    if row is None or row.id == 1 and action == "off":
        await callback.answer("Агентство №1 — основное, его не отключаем", show_alert=True)
        return
    row.status = "disabled" if action == "off" else "active"
    await session.commit()
    register(from_row(row))  # бот агентства сразу перестаёт / начинает отвечать
    await callback.answer("Готово")
    await callback.message.edit_text(_card(row), reply_markup=_toggle_kb(row))
