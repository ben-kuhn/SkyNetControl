"""Shipped roster template defaults.

These constants mirror the state of the seeded row after the
f5b2383f6dd3 (insert) + f62789139379 (genericize) migrations have
applied. They're duplicated here rather than imported by the
migrations because Alembic revisions need to remain frozen — a
migration that imported from this module would silently change
behaviour every time we edited the constants.

A snapshot test (`test_seeds_match_migrations`) keeps the two in
sync: if the body drifts, that test fails.

Exposed via `GET /api/roster/template-defaults` so the
"+ New template" UI can pre-fill from the shipped original even
after operators have edited their own default.
"""
from typing import TypedDict


class SeedRosterTemplate(TypedDict):
    name: str
    subject_template: str
    header_template: str
    welcome_template: str
    comments_template: str
    footer_template: str
    lead_time_days: int


DEFAULT_NET_ROSTER: SeedRosterTemplate = {
    "name": "Default Net Roster",
    "subject_template": "{{ net_callsign }} Winlink Net Roster — {{ date }}",
    "header_template": (
        "{{ net_callsign }} Winlink Net Roster for {{ day_of_week }}, {{ date }}"
        "{% if time %} at {{ time }} UTC{% endif %}.\n"
        "Net Control: {{ net_control }}\n"
        "{% if activity_title %}Activity: {{ activity_title }}{% endif %}\n"
        "Total Check-ins: {{ total_count }}"
    ),
    "welcome_template": (
        "{% for m in new_members %}Welcome to the {{ net_callsign }} Winlink Net, "
        "{{ m.name }} ({{ m.callsign }})!\n{% endfor %}"
    ),
    # f62789139379 didn't touch comments_template; this is the original
    # from f5b2383f6dd3 verbatim (no W0NE branding to genericize).
    "comments_template": (
        "{% for c in checkins %}{% if c.comments %}{{ c.callsign }} ({{ c.name }}): {{ c.comments }}\n"
        "{% endif %}{% endfor %}"
    ),
    "footer_template": (
        "{% if next_week_preview %}Next week: {{ next_week_preview }}\n{% endif %}"
        "{% if map_url %}Check-in details: {{ session_url }}\n{% endif %}"
        "73 de {{ net_callsign }}"
    ),
    "lead_time_days": 1,
}

SEED_ROSTER_TEMPLATES: list[SeedRosterTemplate] = [DEFAULT_NET_ROSTER]
