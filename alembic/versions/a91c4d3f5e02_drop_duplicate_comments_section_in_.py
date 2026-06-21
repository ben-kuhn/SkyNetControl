"""drop duplicate comments section in default roster template

Revision ID: a91c4d3f5e02
Revises: e4833c347829
Create Date: 2026-06-21 12:00:00.000000

Backlog item 6: the shipped default roster template's `comments_template`
re-rendered every check-in's `comments` field as a separate "Comments"
section, which the per-row assembled table already includes. Operators
saw each comment twice.

This migration clears `comments_template` to an empty string ONLY for
rows that still hold the original shipped body — admins who customized
the section to something else (highlights, signoff notes, etc.) are
left alone. `assemble_roster` already skips an empty content_comments.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a91c4d3f5e02"
down_revision: Union[str, None] = "e4833c347829"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_COMMENTS_TEMPLATE = (
    "{% for c in checkins %}{% if c.comments %}{{ c.callsign }} ({{ c.name }}): {{ c.comments }}\n"
    "{% endif %}{% endfor %}"
)
NEW_COMMENTS_TEMPLATE = ""


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roster_templates SET comments_template = :new "
            "WHERE comments_template = :old"
        ).bindparams(old=OLD_COMMENTS_TEMPLATE, new=NEW_COMMENTS_TEMPLATE)
    )


def downgrade() -> None:
    # Only restore rows that we just blanked (still empty + default name).
    op.execute(
        sa.text(
            "UPDATE roster_templates SET comments_template = :old "
            "WHERE comments_template = :new AND name = :name"
        ).bindparams(
            old=OLD_COMMENTS_TEMPLATE,
            new=NEW_COMMENTS_TEMPLATE,
            name="Default Net Roster",
        )
    )
