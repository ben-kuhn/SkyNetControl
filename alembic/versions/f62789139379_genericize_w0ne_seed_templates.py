"""genericize w0ne seed templates

Revision ID: f62789139379
Revises: 4b3cb52b36dd
Create Date: 2026-06-05 17:55:33.929544

Rewrites the W0NE-branded seed rows from f5b2383f6dd3 (roster) and
4d657143fdea (reminders) so the literal "W0NE" and "w0ne@winlink.org"
are replaced with the Jinja vars `{{ net_callsign }}` and
`{{ net_address }}`. Each UPDATE matches on the full original seed
text — admins who have edited their templates are left alone.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f62789139379"
down_revision: Union[str, None] = "4b3cb52b36dd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Roster template (from f5b2383f6dd3) ---

OLD_ROSTER_NAME = "W0NE Net Roster"
NEW_ROSTER_NAME = "Default Net Roster"

OLD_ROSTER_SUBJECT = "W0NE Winlink Net Roster — {{ date }}"
NEW_ROSTER_SUBJECT = "{{ net_callsign }} Winlink Net Roster — {{ date }}"

OLD_ROSTER_HEADER = (
    "W0NE Winlink Net Roster for {{ day_of_week }}, {{ date }}"
    "{% if time %} at {{ time }} UTC{% endif %}.\n"
    "Net Control: {{ net_control }}\n"
    "{% if activity_title %}Activity: {{ activity_title }}{% endif %}\n"
    "Total Check-ins: {{ total_count }}"
)
NEW_ROSTER_HEADER = (
    "{{ net_callsign }} Winlink Net Roster for {{ day_of_week }}, {{ date }}"
    "{% if time %} at {{ time }} UTC{% endif %}.\n"
    "Net Control: {{ net_control }}\n"
    "{% if activity_title %}Activity: {{ activity_title }}{% endif %}\n"
    "Total Check-ins: {{ total_count }}"
)

OLD_ROSTER_WELCOME = (
    "{% for m in new_members %}Welcome to the W0NE Winlink Net, "
    "{{ m.name }} ({{ m.callsign }})!\n{% endfor %}"
)
NEW_ROSTER_WELCOME = (
    "{% for m in new_members %}Welcome to the {{ net_callsign }} Winlink Net, "
    "{{ m.name }} ({{ m.callsign }})!\n{% endfor %}"
)

OLD_ROSTER_FOOTER = (
    "{% if next_week_preview %}Next week: {{ next_week_preview }}\n{% endif %}"
    "{% if map_url %}Check-in details: {{ session_url }}\n{% endif %}"
    "73 de W0NE"
)
NEW_ROSTER_FOOTER = (
    "{% if next_week_preview %}Next week: {{ next_week_preview }}\n{% endif %}"
    "{% if map_url %}Check-in details: {{ session_url }}\n{% endif %}"
    "73 de {{ net_callsign }}"
)

# --- Reminder templates (from 4d657143fdea) ---

OLD_REGULAR_SUBJECT = "W0NE Winlink Net Reminder — {{ date }}"
NEW_REGULAR_SUBJECT = "{{ net_callsign }} Winlink Net Reminder — {{ date }}"

OLD_REGULAR_BODY = (
    "Reminder: the W0NE Winlink Net check-in is this {{ day_of_week }}, {{ date }}.\n\n"
    "Please send your check-in to w0ne@winlink.org with your name, callsign, "
    "city, county, state, and mode.\n\n"
    "Net control: {{ net_control }}\n"
    "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
)
NEW_REGULAR_BODY = (
    "Reminder: the {{ net_callsign }} Winlink Net check-in is this {{ day_of_week }}, {{ date }}.\n\n"
    "Please send your check-in to {{ net_address }} with your name, callsign, "
    "city, county, state, and mode.\n\n"
    "Net control: {{ net_control }}\n"
    "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
)

OLD_ACTIVITY_SUBJECT = "W0NE Winlink Net — {{ activity_title }} — {{ date }}"
NEW_ACTIVITY_SUBJECT = "{{ net_callsign }} Winlink Net — {{ activity_title }} — {{ date }}"

OLD_ACTIVITY_BODY = (
    "This {{ day_of_week }}'s W0NE Winlink Net features a special activity: "
    "**{{ activity_title }}**\n\n"
    "{{ activity_instructions }}\n\n"
    "Please send your check-in to w0ne@winlink.org with your name, callsign, "
    "city, county, state, and mode.\n\n"
    "Net control: {{ net_control }}\n"
    "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
)
NEW_ACTIVITY_BODY = (
    "This {{ day_of_week }}'s {{ net_callsign }} Winlink Net features a special activity: "
    "**{{ activity_title }}**\n\n"
    "{{ activity_instructions }}\n\n"
    "Please send your check-in to {{ net_address }} with your name, callsign, "
    "city, county, state, and mode.\n\n"
    "Net control: {{ net_control }}\n"
    "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
)


def upgrade() -> None:
    # Roster template
    op.execute(
        sa.text(
            "UPDATE roster_templates SET "
            "name = :name_new, "
            "subject_template = :subject_new, "
            "header_template = :header_new, "
            "welcome_template = :welcome_new, "
            "footer_template = :footer_new "
            "WHERE name = :name_old "
            "AND subject_template = :subject_old "
            "AND header_template = :header_old "
            "AND welcome_template = :welcome_old "
            "AND footer_template = :footer_old"
        ).bindparams(
            name_old=OLD_ROSTER_NAME,
            name_new=NEW_ROSTER_NAME,
            subject_old=OLD_ROSTER_SUBJECT,
            subject_new=NEW_ROSTER_SUBJECT,
            header_old=OLD_ROSTER_HEADER,
            header_new=NEW_ROSTER_HEADER,
            welcome_old=OLD_ROSTER_WELCOME,
            welcome_new=NEW_ROSTER_WELCOME,
            footer_old=OLD_ROSTER_FOOTER,
            footer_new=NEW_ROSTER_FOOTER,
        )
    )

    # Reminder: regular
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET "
            "subject_template = :subject_new, "
            "body_template = :body_new "
            "WHERE name = :name "
            "AND subject_template = :subject_old "
            "AND body_template = :body_old"
        ).bindparams(
            name="Regular Check-in Reminder",
            subject_old=OLD_REGULAR_SUBJECT,
            subject_new=NEW_REGULAR_SUBJECT,
            body_old=OLD_REGULAR_BODY,
            body_new=NEW_REGULAR_BODY,
        )
    )

    # Reminder: activity
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET "
            "subject_template = :subject_new, "
            "body_template = :body_new "
            "WHERE name = :name "
            "AND subject_template = :subject_old "
            "AND body_template = :body_old"
        ).bindparams(
            name="Activity Week Reminder",
            subject_old=OLD_ACTIVITY_SUBJECT,
            subject_new=NEW_ACTIVITY_SUBJECT,
            body_old=OLD_ACTIVITY_BODY,
            body_new=NEW_ACTIVITY_BODY,
        )
    )


def downgrade() -> None:
    # Roster
    op.execute(
        sa.text(
            "UPDATE roster_templates SET "
            "name = :name_old, "
            "subject_template = :subject_old, "
            "header_template = :header_old, "
            "welcome_template = :welcome_old, "
            "footer_template = :footer_old "
            "WHERE name = :name_new "
            "AND subject_template = :subject_new "
            "AND header_template = :header_new "
            "AND welcome_template = :welcome_new "
            "AND footer_template = :footer_new"
        ).bindparams(
            name_old=OLD_ROSTER_NAME,
            name_new=NEW_ROSTER_NAME,
            subject_old=OLD_ROSTER_SUBJECT,
            subject_new=NEW_ROSTER_SUBJECT,
            header_old=OLD_ROSTER_HEADER,
            header_new=NEW_ROSTER_HEADER,
            welcome_old=OLD_ROSTER_WELCOME,
            welcome_new=NEW_ROSTER_WELCOME,
            footer_old=OLD_ROSTER_FOOTER,
            footer_new=NEW_ROSTER_FOOTER,
        )
    )

    # Reminder: regular
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET "
            "subject_template = :subject_old, "
            "body_template = :body_old "
            "WHERE name = :name "
            "AND subject_template = :subject_new "
            "AND body_template = :body_new"
        ).bindparams(
            name="Regular Check-in Reminder",
            subject_old=OLD_REGULAR_SUBJECT,
            subject_new=NEW_REGULAR_SUBJECT,
            body_old=OLD_REGULAR_BODY,
            body_new=NEW_REGULAR_BODY,
        )
    )

    # Reminder: activity
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET "
            "subject_template = :subject_old, "
            "body_template = :body_old "
            "WHERE name = :name "
            "AND subject_template = :subject_new "
            "AND body_template = :body_new"
        ).bindparams(
            name="Activity Week Reminder",
            subject_old=OLD_ACTIVITY_SUBJECT,
            subject_new=NEW_ACTIVITY_SUBJECT,
            body_old=OLD_ACTIVITY_BODY,
            body_new=NEW_ACTIVITY_BODY,
        )
    )
