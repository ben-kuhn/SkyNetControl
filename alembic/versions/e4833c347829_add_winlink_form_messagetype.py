"""add winlink_form messagetype

Revision ID: e4833c347829
Revises: ccea830a3f40
Create Date: 2026-06-20 19:10:26.101194

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e4833c347829'
down_revision: Union[str, None] = 'ccea830a3f40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite stores Enum as VARCHAR(N), so adding a value is a no-op at the
    # DB layer — the Python enum extension in models.py is sufficient.
    # PostgreSQL stores it as a native enum type and requires ALTER TYPE.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'winlink_form'")


def downgrade() -> None:
    # PostgreSQL ENUM values cannot be removed without recreating the type.
    # This migration is one-way on Postgres; on SQLite it's already a no-op.
    pass
