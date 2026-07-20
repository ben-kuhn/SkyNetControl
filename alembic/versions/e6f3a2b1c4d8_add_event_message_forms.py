"""add event message forms

Revision ID: e6f3a2b1c4d8
Revises: d5e2f6a1b3c7
Create Date: 2026-07-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e6f3a2b1c4d8'
down_revision: Union[str, None] = 'd5e2f6a1b3c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'event_message_forms',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_message_id', sa.Integer(), nullable=False),
        sa.Column('template_path', sa.String(length=1024), nullable=False),
        sa.Column('display_form', sa.String(length=255), nullable=False),
        sa.Column('reply_template', sa.String(length=255), nullable=True),
        sa.Column('variables', sa.JSON(), nullable=False),
        sa.Column('datetime_stamp', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['event_message_id'], ['event_messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('event_message_forms')
