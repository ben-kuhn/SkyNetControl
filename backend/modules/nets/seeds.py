"""Net seeding helpers.

seed_default_net_content is called by create_net to provision default roster
and reminder templates for a new net.

TODO (Task 9): ReminderTemplate does not yet carry net_id — that is added
when the reminders module becomes net-aware.  Until then, only roster
templates are seeded here.
"""
from sqlalchemy.orm import Session


def seed_default_net_content(db: Session, net_id: int) -> None:
    """Seed default roster templates for *net_id*.

    Idempotent: safe to call more than once (skips existing rows).
    ReminderTemplate seeding will be added in Task 9.
    """
    from backend.modules.roster.seeds import seed_net_roster_templates

    seed_net_roster_templates(db, net_id)
