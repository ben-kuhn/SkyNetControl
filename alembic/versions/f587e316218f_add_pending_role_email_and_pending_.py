"""add pending role email and pending callsign

Revision ID: f587e316218f
Revises: f5b2383f6dd3
Create Date: 2026-04-21 16:48:45.205875

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f587e316218f'
down_revision: Union[str, None] = 'f5b2383f6dd3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to users table
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("pending_callsign", sa.String(20), nullable=True))

    # For SQLite: the enum is stored as VARCHAR, so adding PENDING just works.
    # For PostgreSQL, uncomment:
    # op.execute("ALTER TYPE userrole ADD VALUE 'pending'")


def downgrade() -> None:
    op.drop_column("users", "pending_callsign")
    op.drop_column("users", "email")
