"""Подписки на поиск: clients.notify и таблица search_alerts.

Идемпотентно (как 0002): ревизия 0001 создаёт таблицы по ТЕКУЩИМ моделям, поэтому
на свежей БД всё уже есть, а на существующей (обновление) — добавляем.

Revision ID: 0003_search_alerts
Revises: 0002_add_language
Create Date: 2026-10-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_search_alerts"
down_revision: Union[str, None] = "0002_add_language"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return column in {col["name"] for col in _inspector().get_columns(table)}


def upgrade() -> None:
    if not _has_column("clients", "notify"):
        op.add_column("clients", sa.Column("notify", sa.Boolean(), server_default=sa.true(), nullable=False))
    if "search_alerts" not in _inspector().get_table_names():
        op.create_table(
            "search_alerts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("telegram_id", sa.BigInteger(), nullable=False),
            sa.Column("client_id", sa.String(length=20),
                      sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
            sa.Column("property_id", sa.String(length=20),
                      sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("telegram_id", "property_id", name="uq_alert_user_property"),
        )
        op.create_index("ix_search_alerts_telegram_id", "search_alerts", ["telegram_id"])
        op.create_index("ix_search_alerts_client_id", "search_alerts", ["client_id"])
        op.create_index("ix_search_alerts_property_id", "search_alerts", ["property_id"])
        op.create_index("ix_search_alerts_sent_at", "search_alerts", ["sent_at"])


def downgrade() -> None:
    if "search_alerts" in _inspector().get_table_names():
        op.drop_table("search_alerts")
    if _has_column("clients", "notify"):
        op.drop_column("clients", "notify")
