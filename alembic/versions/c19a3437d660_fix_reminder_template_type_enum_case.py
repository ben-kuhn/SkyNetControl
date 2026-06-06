"""fix reminder template_type enum case

Revision ID: c19a3437d660
Revises: f62789139379
Create Date: 2026-06-05 19:17:14.389124

The seed rows in 4d657143fdea inserted lowercase enum *values*
('regular_checkin', 'activity') into reminder_templates.template_type,
but the column is declared with uppercase enum *names* (matching what
SQLAlchemy's default Enum(TemplateType) writes for ORM inserts). The
bad rows currently cause `LookupError: 'regular_checkin' is not among
the defined enum values` whenever the ORM tries to read them — which
is why the seeded default templates couldn't be used.

This migration normalizes those legacy values. Any rows already in
the correct uppercase form (post-fix or created via the app) are
untouched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c19a3437d660"
down_revision: Union[str, None] = "f62789139379"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET template_type = 'REGULAR_CHECKIN' "
            "WHERE template_type = 'regular_checkin'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET template_type = 'ACTIVITY' "
            "WHERE template_type = 'activity'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET template_type = 'regular_checkin' "
            "WHERE template_type = 'REGULAR_CHECKIN'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET template_type = 'activity' "
            "WHERE template_type = 'ACTIVITY'"
        )
    )
