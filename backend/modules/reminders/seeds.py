"""Shipped reminder template defaults.

These constants mirror the state of the seeded rows after the
4d657143fdea (insert) + f62789139379 (genericize) migrations have
applied. They're duplicated here rather than imported by the
migrations because Alembic revisions need to remain frozen — a
migration that imported from this module would silently change
behaviour every time we edited the constants.

A snapshot test (`test_seeds_match_migrations`) keeps the two in
sync: if either set of bodies drifts, that test fails.

Exposed via `GET /api/nets/{net_slug}/reminders/template-defaults` so the
"+ New template" UI can pre-fill from the shipped originals even
after operators have edited their own defaults.
"""
from typing import TypedDict


class SeedReminderTemplate(TypedDict):
    name: str
    template_type: str
    subject_template: str
    body_template: str
    lead_time_days: int


REGULAR_CHECKIN: SeedReminderTemplate = {
    "name": "Regular Check-in Reminder",
    "template_type": "regular_checkin",
    "subject_template": "{{ net_callsign }} Winlink Net Reminder — {{ date }}",
    "body_template": (
        "Reminder: the {{ net_callsign }} Winlink Net check-in is this "
        "{{ day_of_week }}, {{ date }}.\n\n"
        "Please send your check-in to {{ net_address }} with your name, callsign, "
        "city, county, state, and mode.\n\n"
        "Net control: {{ net_control }}\n"
        "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
    ),
    "lead_time_days": 2,
}

ACTIVITY: SeedReminderTemplate = {
    "name": "Activity Week Reminder",
    "template_type": "activity",
    "subject_template": "{{ net_callsign }} Winlink Net — {{ activity_title }} — {{ date }}",
    "body_template": (
        "This {{ day_of_week }}'s {{ net_callsign }} Winlink Net features a special activity: "
        "**{{ activity_title }}**\n\n"
        "{{ activity_instructions }}\n\n"
        "Please send your check-in to {{ net_address }} with your name, callsign, "
        "city, county, state, and mode.\n\n"
        "Net control: {{ net_control }}\n"
        "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
    ),
    "lead_time_days": 2,
}

SEED_REMINDER_TEMPLATES: list[SeedReminderTemplate] = [REGULAR_CHECKIN, ACTIVITY]


def seed_net_reminder_templates(db, net_id: int) -> None:
    """Insert the default reminder template set for *net_id*.

    Called by ``backend.modules.nets.seeds.seed_default_net_content`` when a new
    net is created.  Idempotent: skips templates whose (net_id, name) pair already
    exists so it is safe to call more than once.
    """
    from sqlalchemy.orm import Session as _Session
    from backend.modules.reminders.models import ReminderTemplate, TemplateType

    assert isinstance(db, _Session)
    for i, seed in enumerate(SEED_REMINDER_TEMPLATES):
        existing = (
            db.query(ReminderTemplate)
            .filter(ReminderTemplate.net_id == net_id, ReminderTemplate.name == seed["name"])
            .one_or_none()
        )
        if existing is not None:
            continue
        tmpl = ReminderTemplate(
            net_id=net_id,
            name=seed["name"],
            template_type=TemplateType(seed["template_type"]),
            subject_template=seed["subject_template"],
            body_template=seed["body_template"],
            lead_time_days=seed["lead_time_days"],
            is_default=(i == 0),  # first seed (regular_checkin) is the default
        )
        db.add(tmpl)
    db.flush()
