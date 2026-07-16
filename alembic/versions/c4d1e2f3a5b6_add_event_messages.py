"""add event messages

Revision ID: c4d1e2f3a5b6
Revises: b3f0a1c2d4e5
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d1e2f3a5b6'
down_revision: Union[str, None] = 'b3f0a1c2d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('msg_seq', sa.Integer(), nullable=False, server_default='0'))
    op.create_table(
        'event_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('msg_seq', sa.Integer(), nullable=False),
        sa.Column('direction', sa.Enum('INBOUND', 'OUTBOUND', name='messagedirection'), nullable=False),
        sa.Column('raw_message_id', sa.Integer(), nullable=True),
        sa.Column('participant_id', sa.Integer(), nullable=True),
        sa.Column('from_callsign', sa.String(length=64), nullable=False),
        sa.Column('to_address', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.Enum('UNREAD', 'READ', 'DISMISSED', name='messagestatus'), nullable=False),
        sa.Column('reply_to_id', sa.Integer(), nullable=True),
        sa.Column('actor', sa.String(length=20), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
        sa.ForeignKeyConstraint(['raw_message_id'], ['raw_messages.id'], ),
        sa.ForeignKeyConstraint(['participant_id'], ['event_participants.id'], ),
        sa.ForeignKeyConstraint(['reply_to_id'], ['event_messages.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'raw_message_id', name='uq_event_messages_event_raw'),
    )


def downgrade() -> None:
    op.drop_table('event_messages')
    op.drop_column('events', 'msg_seq')
