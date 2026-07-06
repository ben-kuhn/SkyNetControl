"""roster header: skip stray comma when day_of_week is empty

Revision ID: 9a1f8e3c40b2
Revises: 7bc4a2f1e0d9
Create Date: 2026-07-06 00:00:00.000000

The shipped default header rendered ``Winlink Net Roster for , June 29,
2026`` when ``day_of_week`` was empty (real events / no-season sessions
/ misconfigured seasons). Wrap the day+comma in a Jinja ``if`` so the
comma disappears when the value is empty.

Only updates rows still holding the exact prior default — customised
headers are left alone.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9a1f8e3c40b2"
down_revision: Union[str, None] = "7bc4a2f1e0d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_HEADER = (
    "{{ net_callsign }} Winlink Net Roster for {{ day_of_week }}, {{ date }}"
    "{% if time %} at {{ time }} UTC{% endif %}.\n"
    "Net Control: {{ net_control }}\n"
    "{% if activity_title %}Activity: {{ activity_title }}{% endif %}\n"
    "Total Check-ins: {{ total_count }}"
)
NEW_HEADER = (
    "{{ net_callsign }} Winlink Net Roster for "
    "{% if day_of_week %}{{ day_of_week }}, {% endif %}{{ date }}"
    "{% if time %} at {{ time }} UTC{% endif %}.\n"
    "Net Control: {{ net_control }}\n"
    "{% if activity_title %}Activity: {{ activity_title }}{% endif %}\n"
    "Total Check-ins: {{ total_count }}"
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roster_templates SET header_template = :new "
            "WHERE header_template = :old"
        ).bindparams(old=OLD_HEADER, new=NEW_HEADER)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roster_templates SET header_template = :old "
            "WHERE header_template = :new"
        ).bindparams(old=OLD_HEADER, new=NEW_HEADER)
    )
