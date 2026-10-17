"""Аналитика: таблица events.

Идемпотентно (как 0002–0004): на свежей БД таблицу уже создала ревизия 0001.

Revision ID: 0005_events
Revises: 0004_freshness
Create Date: 2026-10-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_events"
down_revision: Union[str, None] = "0004_freshness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "events" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("property_id", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("type", "telegram_id", "property_id", "created_at"):
        op.create_index(f"ix_events_{column}", "events", [column])


def downgrade() -> None:
    if "events" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("events")
