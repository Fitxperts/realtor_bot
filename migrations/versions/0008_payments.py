"""Счета на подписку и транзакции Payme / Click.

Идемпотентно (как 0002–0007): на свежей БД таблицу уже создала ревизия 0001.

Revision ID: 0008_payments
Revises: 0007_billing
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_payments"
down_revision: Union[str, None] = "0007_billing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "payments" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("agency_id", sa.BigInteger(), sa.ForeignKey("agencies.id"), nullable=False),
        sa.Column("plan", sa.String(16), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("payer_id", sa.BigInteger()),
        sa.Column("provider", sa.String(8)),
        sa.Column("ext_id", sa.String(64)),
        sa.Column("ext_time", sa.BigInteger()),
        sa.Column("state", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Integer()),
        sa.Column("create_time", sa.BigInteger()),
        sa.Column("perform_time", sa.BigInteger()),
        sa.Column("cancel_time", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_payments_agency_id", "payments", ["agency_id"])
    op.create_index("ix_payments_ext_id", "payments", ["ext_id"])


def downgrade() -> None:
    if "payments" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("payments")
