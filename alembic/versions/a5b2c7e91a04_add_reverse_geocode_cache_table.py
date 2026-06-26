"""add reverse_geocode_cache table

Revision ID: a5b2c7e91a04
Revises: d7e2b1f43c01
Create Date: 2026-06-26 13:00:00

Cache of (lat, lon) -> closest populated place lookups against the
Overpass API. Lat/lon are rounded to two decimals (~1 km) as the key
so repeat check-ins from the same operator share a cache row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a5b2c7e91a04"
down_revision: Union[str, None] = "d7e2b1f43c01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reverse_geocode_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # Rounded-to-2-decimals lat/lon as integer hundredths — integers
        # avoid floating-point equality issues in the unique constraint.
        sa.Column("lat_key", sa.Integer(), nullable=False),
        sa.Column("lon_key", sa.Integer(), nullable=False),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("lat_key", "lon_key", name="ux_reverse_geocode_cache_key"),
    )


def downgrade() -> None:
    op.drop_table("reverse_geocode_cache")
