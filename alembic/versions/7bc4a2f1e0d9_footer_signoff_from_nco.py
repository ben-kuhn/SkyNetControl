"""roster footer sign-off from NCO instead of net callsign

Revision ID: 7bc4a2f1e0d9
Revises: cc6d665bb77e
Create Date: 2026-07-05 00:00:00.000000

The shipped default roster template signed off ``73 de {{ net_callsign }}``
which rendered as the net's identifier (e.g. ``73 de W0NE``). Operators
expect the sign-off to name the actual net-control operator for the
session; ``{{ net_callsign }}`` still appears so recipients see which
net the message is from.

Only updates rows still holding the exact prior default — anyone who
customised their footer keeps their edits.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7bc4a2f1e0d9"
down_revision: Union[str, None] = "cc6d665bb77e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_FOOTER = (
    "{% if next_week_preview %}Next week: {{ next_week_preview }}\n{% endif %}"
    "{% if map_url %}Check-in details: {{ session_url }}\n{% endif %}"
    "73 de {{ net_callsign }}"
)
NEW_FOOTER = (
    "{% if next_week_preview %}Next week: {{ next_week_preview }}\n{% endif %}"
    "{% if map_url %}Check-in details: {{ session_url }}\n{% endif %}"
    "73 de {{ net_control }}, {{ net_callsign }} Net Control"
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roster_templates SET footer_template = :new "
            "WHERE footer_template = :old"
        ).bindparams(old=OLD_FOOTER, new=NEW_FOOTER)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roster_templates SET footer_template = :old "
            "WHERE footer_template = :new"
        ).bindparams(old=OLD_FOOTER, new=NEW_FOOTER)
    )
