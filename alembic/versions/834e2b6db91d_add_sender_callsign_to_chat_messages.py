"""add sender_callsign to chat_messages

Revision ID: 834e2b6db91d
Revises: 9a1f8e3c40b2
Create Date: 2026-07-14 13:48:47.202955

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '834e2b6db91d'
down_revision: Union[str, None] = '9a1f8e3c40b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("sender_callsign", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "sender_callsign")
