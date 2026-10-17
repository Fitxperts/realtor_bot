"""White-label: таблица agencies и agency_id во всех таблицах агентства.

Текущее агентство становится агентством №1: все существующие строки получают
agency_id = 1. У bot_users ключ становится (agency_id, telegram_id) — один
человек может пользоваться ботами разных агентств.

Идемпотентно (как 0002–0005): на свежей БД таблицы и колонки уже создала ревизия
0001 по текущим моделям — здесь только гарантируем агентство №1 и счётчик ID.

Revision ID: 0006_agencies
Revises: 0005_events
Create Date: 2026-10-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_agencies"
down_revision: Union[str, None] = "0005_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("properties", "clients", "meetings", "bot_users", "search_alerts", "events")


def _insp():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "agencies" not in _insp().get_table_names():
        op.create_table(
            "agencies",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("city", sa.String(64)),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("bot_token_enc", sa.Text()),
            sa.Column("bot_id", sa.BigInteger(), unique=True),
            sa.Column("bot_username", sa.String(64)),
            sa.Column("webhook_secret", sa.String(64)),
            sa.Column("channel_id", sa.String(64)),
            sa.Column("channel_url", sa.String(255)),
            sa.Column("contacts", sa.String(255)),
            sa.Column("watermark", sa.String(64)),
            sa.Column("admin_ids", sa.String(255)),
            sa.Column("owner_telegram_id", sa.BigInteger()),
            sa.Column("language", sa.String(2), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_agencies_owner_telegram_id", "agencies", ["owner_telegram_id"])

    # Агентство №1 — то, что работало до SaaS (настройки пока из .env)
    if bind.execute(sa.text("SELECT 1 FROM agencies WHERE id = 1")).first() is None:
        bind.execute(sa.text(
            "INSERT INTO agencies (id, name, status, language, created_at) "
            "VALUES (1, 'Агентство №1', 'active', 'uz', CURRENT_TIMESTAMP)"
        ))
    if bind.dialect.name == "postgresql":  # явный id=1 не двигает счётчик — иначе следующее получит 1
        bind.execute(sa.text(
            "SELECT setval(pg_get_serial_sequence('agencies', 'id'), (SELECT MAX(id) FROM agencies))"
        ))

    for table in TENANT_TABLES:
        if not _has_column(table, "agency_id"):
            op.add_column(table, sa.Column("agency_id", sa.BigInteger(), server_default="1", nullable=False))
            op.create_foreign_key(f"{table}_agency_id_fkey", table, "agencies", ["agency_id"], ["id"])
            if table != "bot_users":  # у bot_users agency_id — начало первичного ключа, индекс не нужен
                op.create_index(f"ix_{table}_agency_id", table, ["agency_id"])

    pk = _insp().get_pk_constraint("bot_users")
    if pk["constrained_columns"] != ["agency_id", "telegram_id"]:
        op.drop_constraint(pk["name"] or "bot_users_pkey", "bot_users", type_="primary")
        op.create_primary_key("bot_users_pkey", "bot_users", ["agency_id", "telegram_id"])


def downgrade() -> None:
    pk = _insp().get_pk_constraint("bot_users")
    if pk["constrained_columns"] == ["agency_id", "telegram_id"]:
        op.drop_constraint(pk["name"] or "bot_users_pkey", "bot_users", type_="primary")
        op.create_primary_key("bot_users_pkey", "bot_users", ["telegram_id"])
    for table in TENANT_TABLES:
        if _has_column(table, "agency_id"):
            op.drop_constraint(f"{table}_agency_id_fkey", table, type_="foreignkey")
            if f"ix_{table}_agency_id" in {i["name"] for i in _insp().get_indexes(table)}:
                op.drop_index(f"ix_{table}_agency_id", table_name=table)
            op.drop_column(table, "agency_id")
    if "agencies" in _insp().get_table_names():
        op.drop_table("agencies")
