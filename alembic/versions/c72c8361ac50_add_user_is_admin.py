"""add user is_admin

Revision ID: c72c8361ac50
Revises: a91c4d3f5e02
Create Date: 2026-06-22 15:47:52.884851

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c72c8361ac50'
down_revision: Union[str, None] = 'a91c4d3f5e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE users SET is_admin = 1 WHERE role = 'admin'")


def downgrade() -> None:
    op.drop_column("users", "is_admin")
