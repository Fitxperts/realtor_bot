"""Код языка пользователя длиннее двух букв: узбекская кириллица — «uz_cyrl».

Revision ID: 0010_lang_code
Revises: 0009_districts
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_lang_code"
down_revision: Union[str, None] = "0009_districts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Расширение VARCHAR безопасно и идемпотентно (на свежей БД колонка уже String(8))
    op.alter_column("bot_users", "language", existing_type=sa.String(2), type_=sa.String(8),
                    existing_nullable=True)


def downgrade() -> None:
    op.get_bind().execute(sa.text("UPDATE bot_users SET language = 'uz' WHERE language = 'uz_cyrl'"))
    op.alter_column("bot_users", "language", existing_type=sa.String(8), type_=sa.String(2),
                    existing_nullable=True)
