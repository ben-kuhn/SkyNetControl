"""add nco_reminder_logs table

Revision ID: c2d3e4f5a6b7
Revises: b0e1c2d3f4a5
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b0e1c2d3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nco_reminder_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Enum("MORNING", "EVENING", name="ncoreminderkind"), nullable=False),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["net_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "kind", name="uq_nco_reminder_logs_session_kind"),
    )


def downgrade() -> None:
    op.drop_table("nco_reminder_logs")