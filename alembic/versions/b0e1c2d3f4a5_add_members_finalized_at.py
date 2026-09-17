"""add members_finalized_at to net_sessions

Revision ID: b0e1c2d3f4a5
Revises: a7b1c9d3e5f0
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b0e1c2d3f4a5"
down_revision: Union[str, None] = "a7b1c9d3e5f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "net_sessions",
        sa.Column("members_finalized_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("net_sessions", "members_finalized_at")