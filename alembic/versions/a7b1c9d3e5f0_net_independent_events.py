"""net-independent events (reset)

Revision ID: a7b1c9d3e5f0
Revises: a1b2c3d4e5f6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b1c9d3e5f0"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Reset: delete all existing events + child rows (clean slate, no preservation).
    #    event-scoped PAT sessions first (FK to events), then event children, then events.
    op.execute("UPDATE pat_connection_sessions SET event_id = NULL WHERE event_id IS NOT NULL")
    op.execute("DELETE FROM event_message_forms")
    op.execute("DELETE FROM event_messages")
    op.execute("DELETE FROM event_log")
    op.execute("DELETE FROM event_participants")
    op.execute("DELETE FROM event_posts")
    op.execute("DELETE FROM events")

    # 2. New tables.
    op.create_table(
        "event_operators",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("callsign", sa.String(length=20), nullable=False),
        sa.Column("added_by", sa.String(length=20), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "callsign", name="uq_event_operator"),
    )
    op.create_table(
        "event_config",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "key"),
    )

    # 3. events: add ownership columns, drop net_id.
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("public_token", sa.String(length=64), nullable=False, server_default=""))
        batch.add_column(sa.Column("visibility", sa.String(length=16), nullable=False, server_default="private"))
        batch.create_index("ix_events_public_token", ["public_token"])
        batch.drop_column("net_id")
    # server_default was only to satisfy the NOT NULL add on an (now empty) table; drop it.
    with op.batch_alter_table("events") as batch:
        batch.alter_column("public_token", server_default=None)
        batch.alter_column("visibility", server_default=None)

    # 4. pat_connection_sessions.net_id -> nullable.
    with op.batch_alter_table("pat_connection_sessions") as batch:
        batch.alter_column("net_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("pat_connection_sessions") as batch:
        batch.alter_column("net_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("events") as batch:
        batch.drop_index("ix_events_public_token")
        batch.add_column(sa.Column("net_id", sa.Integer(), nullable=True))  # data gone; nullable on downgrade
        batch.drop_column("visibility")
        batch.drop_column("public_token")
    op.drop_table("event_config")
    op.drop_table("event_operators")
