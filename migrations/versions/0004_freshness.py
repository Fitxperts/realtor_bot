"""Проверка актуальности объявлений: properties.freshness_asked_at, confirmed_at.

Идемпотентно (как 0002/0003): на свежей БД колонки уже созданы ревизией 0001.

Revision ID: 0004_freshness
Revises: 0003_search_alerts
Create Date: 2026-10-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_freshness"
down_revision: Union[str, None] = "0003_search_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("freshness_asked_at", "confirmed_at")


def _has_column(table: str, column: str) -> bool:
    return column in {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for name in _COLUMNS:
        if not _has_column("properties", name):
            op.add_column("properties", sa.Column(name, sa.DateTime(), nullable=True))


def downgrade() -> None:
    for name in _COLUMNS:
        if _has_column("properties", name):
            op.drop_column("properties", name)
