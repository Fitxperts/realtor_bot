"""Подписки агентств: тариф, пробный период, «оплачено до», последнее напоминание.

Идемпотентно (как 0002–0006).

Revision ID: 0007_billing
Revises: 0006_agencies
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_billing"
down_revision: Union[str, None] = "0006_agencies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("plan", sa.String(16)),
    ("trial_until", sa.DateTime()),
    ("paid_until", sa.DateTime()),
    ("billing_notice", sa.String(32)),
)


def _has(column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agencies")}


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        if not _has(name):
            op.add_column("agencies", sa.Column(name, type_, nullable=True))
    op.get_bind().execute(sa.text("UPDATE agencies SET plan = 'start' WHERE plan IS NULL"))
    op.alter_column("agencies", "plan", existing_type=sa.String(16), nullable=False)


def downgrade() -> None:
    for name, _ in _COLUMNS:
        if _has(name):
            op.drop_column("agencies", name)
