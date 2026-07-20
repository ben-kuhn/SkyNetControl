"""add pat transport

Revision ID: a1b2c3d4e5f6
Revises: e6f3a2b1c4d8
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e6f3a2b1c4d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Fix 1: on PostgreSQL, delivery_logs.status is a native enum type
    # "deliverystatus" created in d50a22f251e3 with labels PENDING/SENT/FAILED.
    # Adding QUEUED to the Python enum is not enough — extend the PG type here.
    # SQLite stores a bare VARCHAR so this branch must be skipped there.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE deliverystatus ADD VALUE IF NOT EXISTS 'QUEUED'")

    op.create_table(
        "pat_connection_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("net_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("connect_url", sa.String(length=512), nullable=False),
        sa.Column("method_label", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("received_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["net_id"], ["nets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # delivery_logs.status on SQLite is a bare VARCHAR (no CHECK constraint),
    # so "queued" can be inserted without rebuilding the column.
    # We still use batch_alter_table to add the two PAT columns and their FK.
    with op.batch_alter_table("delivery_logs") as batch:
        batch.add_column(sa.Column("pat_session_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pat_mid", sa.String(length=64), nullable=True))
        batch.create_foreign_key(
            "fk_delivery_pat_session", "pat_connection_sessions",
            ["pat_session_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("delivery_logs") as batch:
        batch.drop_constraint("fk_delivery_pat_session", type_="foreignkey")
        batch.drop_column("pat_mid")
        batch.drop_column("pat_session_id")
    op.drop_table("pat_connection_sessions")
    # Note: PostgreSQL does not support removing a value from an enum type,
    # so the 'QUEUED' label added in upgrade() is left in place on downgrade.
