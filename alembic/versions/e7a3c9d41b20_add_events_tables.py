"""add events tables

Revision ID: e7a3c9d41b20
Revises: 834e2b6db91d
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a3c9d41b20'
down_revision: Union[str, None] = '834e2b6db91d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('net_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('event_type', sa.Enum('PUBLIC_SERVICE', 'EMERGENCY', name='eventtype'), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'ACTIVE', 'CLOSED', name='eventstatus'), nullable=False),
    sa.Column('scheduled_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('log_seq', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['net_id'], ['nets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('event_posts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('lat', sa.Float(), nullable=True),
    sa.Column('lon', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'name', name='uq_event_posts_event_name')
    )
    op.create_table('event_participants',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('callsign', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('post_id', sa.Integer(), nullable=True),
    sa.Column('location', sa.Text(), nullable=True),
    sa.Column('current_status', sa.Enum('CHECKED_IN', 'AT_POST', 'EN_ROUTE', 'OUT_OF_SERVICE', 'CHECKED_OUT', name='participantstatus'), nullable=False),
    sa.Column('checked_in_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('checked_out_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
    sa.ForeignKeyConstraint(['post_id'], ['event_posts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'callsign', name='uq_event_participants_event_callsign')
    )
    op.create_table('event_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('entry_type', sa.Enum('SYSTEM', 'NOTE', 'PARTICIPANT_NOTE', name='eventlogtype'), nullable=False),
    sa.Column('callsign', sa.String(length=20), nullable=True),
    sa.Column('actor', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('new_status', sa.Enum('CHECKED_IN', 'AT_POST', 'EN_ROUTE', 'OUT_OF_SERVICE', 'CHECKED_OUT', name='participantstatus'), nullable=True),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'seq', name='uq_event_log_event_seq')
    )


def downgrade() -> None:
    op.drop_table('event_log')
    op.drop_table('event_participants')
    op.drop_table('event_posts')
    op.drop_table('events')
