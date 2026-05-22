"""rename map_url to session_url

Revision ID: 064082225c20
Revises: 96a06deb4e8f
Create Date: 2026-05-22 15:54:28.679300

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '064082225c20'
down_revision: Union[str, None] = '96a06deb4e8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("roster_logs") as batch_op:
        batch_op.alter_column("map_url", new_column_name="session_url")

    op.execute(
        "UPDATE roster_templates "
        "SET footer_template = REPLACE(footer_template, '{{ map_url }}', '{{ session_url }}')"
    )
    op.execute(
        "UPDATE roster_templates "
        "SET footer_template = REPLACE(footer_template, 'Check-in map:', 'Check-in details:')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE roster_templates "
        "SET footer_template = REPLACE(footer_template, 'Check-in details:', 'Check-in map:')"
    )
    op.execute(
        "UPDATE roster_templates "
        "SET footer_template = REPLACE(footer_template, '{{ session_url }}', '{{ map_url }}')"
    )

    with op.batch_alter_table("roster_logs") as batch_op:
        batch_op.alter_column("session_url", new_column_name="map_url")
