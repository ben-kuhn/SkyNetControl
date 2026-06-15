"""drop FK on audit_log.actor_callsign for recovery actor support

Revision ID: e837ac271887
Revises: e59bb622cf36
Create Date: 2026-06-15 13:34:39.616116

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e837ac271887'
down_revision: Union[str, None] = 'e59bb622cf36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 4 introduces a `recovery:<hash-prefix>` actor for audit entries
    # made during a recovery-cookie session. That actor isn't a row in the
    # `users` table, so the existing FK on audit_log.actor_callsign blocks
    # the write on PostgreSQL (and only succeeds on SQLite because FK
    # enforcement is off by default). Drop the constraint; keep the column.
    #
    # The FK's auto-generated name varies by backend (SQLite often leaves
    # it unnamed; PostgreSQL picks something like `audit_log_actor_callsign_fkey`).
    # Use the inspector to discover the actual name at migration time.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    fk_name = None
    for fk in inspector.get_foreign_keys("audit_log"):
        if fk.get("constrained_columns") == ["actor_callsign"] and fk.get("referred_table") == "users":
            fk_name = fk.get("name")
            break

    if fk_name:
        with op.batch_alter_table("audit_log") as batch:
            batch.drop_constraint(fk_name, type_="foreignkey")
    else:
        # No name reflected (typical on SQLite). batch_alter_table with
        # recreate="always" rebuilds the table from the current model — which
        # no longer declares a ForeignKey, so the new table won't have one.
        with op.batch_alter_table("audit_log", recreate="always"):
            pass


def downgrade() -> None:
    # Recreating the FK would require every actor_callsign value to be in the
    # users table, which is no longer guaranteed once recovery actors land —
    # so this downgrade is best-effort and may fail on a populated db.
    with op.batch_alter_table("audit_log") as batch:
        batch.create_foreign_key(
            "audit_log_actor_callsign_fkey",
            "users",
            ["actor_callsign"],
            ["callsign"],
        )
