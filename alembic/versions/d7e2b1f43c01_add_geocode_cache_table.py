"""add geocode_cache table

Revision ID: d7e2b1f43c01
Revises: 859ed3de034e
Create Date: 2026-06-24 13:00:00

Cache of (city, state, country) -> lat/lon resolutions against
Nominatim. Negative results are stored with NULL coordinates so we
don't re-query the same misspelling every check-in.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d7e2b1f43c01"
down_revision: Union[str, None] = "859ed3de034e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Skip if the table already exists. Same SQLite non-transactional DDL
    # hazard as 3c8e5fa10001: legacy DBs can have the table created by an
    # old partial deploy with alembic_version not bumped to here, and the
    # retry then trips "table already exists." Inspector check makes this a
    # no-op when the table is present.
    bind = op.get_bind()
    if "geocode_cache" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "geocode_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("city_norm", sa.String(length=255), nullable=False),
        sa.Column("state_norm", sa.String(length=100), nullable=False),
        sa.Column("country_norm", sa.String(length=64), nullable=False),
        sa.Column("city", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=100), nullable=False),
        sa.Column("country", sa.String(length=64), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "city_norm", "state_norm", "country_norm", name="ux_geocode_cache_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("geocode_cache")
