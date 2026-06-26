"""Net seeding helpers.

seed_default_net_content is called by create_net to provision default roster
and reminder templates for a new net.
"""
from sqlalchemy.orm import Session


def seed_default_net_content(db: Session, net_id: int) -> None:
    """Seed default roster and reminder templates for *net_id*.

    Idempotent: safe to call more than once (skips existing rows).
    """
    from backend.modules.roster.seeds import seed_net_roster_templates
    from backend.modules.reminders.seeds import seed_net_reminder_templates

    seed_net_roster_templates(db, net_id)
    seed_net_reminder_templates(db, net_id)
