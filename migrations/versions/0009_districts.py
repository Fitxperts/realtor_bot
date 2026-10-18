"""Свои районы/массивы агентства (для городов без встроенного справочника).

Идемпотентно (как 0002–0008).

Revision ID: 0009_districts
Revises: 0008_payments
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_districts"
down_revision: Union[str, None] = "0008_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has() -> bool:
    return "districts" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agencies")}


def upgrade() -> None:
    if not _has():
        op.add_column("agencies", sa.Column("districts", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has():
        op.drop_column("agencies", "districts")
