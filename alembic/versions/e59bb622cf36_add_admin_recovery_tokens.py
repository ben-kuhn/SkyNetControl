"""add admin recovery tokens

Revision ID: e59bb622cf36
Revises: 603f5040bba2
Create Date: 2026-06-15 12:43:46.660368

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e59bb622cf36'
down_revision: Union[str, None] = '603f5040bba2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_recovery_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_admin_recovery_tokens_token_hash",
        "admin_recovery_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_recovery_tokens_token_hash", table_name="admin_recovery_tokens")
    op.drop_table("admin_recovery_tokens")
