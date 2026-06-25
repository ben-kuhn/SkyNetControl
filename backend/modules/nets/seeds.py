"""Net seeding helpers.

seed_default_net_content is called by create_net to provision default roster
and reminder templates for a new net.

TODO (Tasks 8/9): RosterTemplate and ReminderTemplate models currently lack a
net_id FK column — that is added when those modules become net-aware.  Until
then, seed_default_net_content is a no-op stub.  Once the FK is in place,
implement this function to insert copies of the seed templates from
backend.modules.roster.seeds and backend.modules.reminders.seeds with the
given net_id.
"""
from sqlalchemy.orm import Session


def seed_default_net_content(db: Session, net_id: int) -> None:  # noqa: ARG001
    """Seed default roster and reminder templates for *net_id*.

    Currently a no-op: RosterTemplate / ReminderTemplate do not yet carry
    net_id (Tasks 8/9).  The Default Net's templates were backfilled by the
    multi_net_cutover migration; new nets will inherit templates once those
    modules are refactored.
    """
