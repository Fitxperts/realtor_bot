"""WhatsApp Cloud API агентства: номер, ключи (зашифрованы), verify token.

Идемпотентно (как 0002–0010).

Revision ID: 0011_whatsapp
Revises: 0010_lang_code
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_whatsapp"
down_revision: Union[str, None] = "0010_lang_code"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("wa_phone_id", sa.String(32)),
    ("wa_token_enc", sa.Text()),
    ("wa_secret_enc", sa.Text()),
    ("wa_verify_token", sa.String(64)),
)


def _has(column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agencies")}


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        if not _has(name):
            op.add_column("agencies", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in _COLUMNS:
        if _has(name):
            op.drop_column("agencies", name)
