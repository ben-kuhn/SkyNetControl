"""add event aprs settings

Revision ID: b3f0a1c2d4e5
Revises: e7a3c9d41b20
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f0a1c2d4e5'
down_revision: Union[str, None] = 'e7a3c9d41b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL adds to an existing table need a server_default for old rows.
    op.add_column('events', sa.Column('aprs_other_stations', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('events', sa.Column('aprs_range_lat', sa.Float(), nullable=True))
    op.add_column('events', sa.Column('aprs_range_lon', sa.Float(), nullable=True))
    op.add_column('events', sa.Column('aprs_range_km', sa.Float(), nullable=True))
    op.add_column('events', sa.Column('aprs_beacon_posts', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('events', 'aprs_beacon_posts')
    op.drop_column('events', 'aprs_range_km')
    op.drop_column('events', 'aprs_range_lon')
    op.drop_column('events', 'aprs_range_lat')
    op.drop_column('events', 'aprs_other_stations')
