"""add net_id to net_sessions

Revision ID: 3c8e5fa10001
Revises: a5b2c7e91a04
Create Date: 2026-06-27 16:00:00

REAL_EVENT sessions have ``season_id=None`` by design — they're standalone
incidents, not part of a recurring net schedule. The original multi-net
cutover routed every session→net lookup through ``season.net_id``, which
makes orphaned (REAL_EVENT) sessions unattributable and thus permanently
unreachable: ``get_session(id, net_id=ctx.net.id)`` returns ``None`` for
them, so every per-session GET/PATCH/DELETE returns 404.

Fix: give ``net_sessions`` its own ``net_id`` column. Sessions with a
season backfill from ``season.net_id``; orphans go to the Default Net
(id=1, which the multi-net cutover guarantees exists).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3c8e5fa10001"
down_revision: Union[str, None] = "a5b2c7e91a04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add the column nullable so the backfill can run.
    #
    # SQLite has non-transactional DDL — if a prior run of this migration
    # succeeded on ADD COLUMN but failed before alembic could stamp the new
    # version, the column sits there NULL with no FK/index and the retry
    # blows up with "duplicate column name". Detect that state and skip the
    # ADD; the rest of the migration (backfill UPDATEs, NOT NULL/FK/index
    # via batch recreate) is already idempotent and will complete the work.
    existing_columns = {c["name"] for c in sa.inspect(bind).get_columns("net_sessions")}
    if "net_id" not in existing_columns:
        with op.batch_alter_table("net_sessions") as batch:
            batch.add_column(sa.Column("net_id", sa.Integer(), nullable=True))

    # 2. Backfill from seasons where possible.
    op.execute(
        """
        UPDATE net_sessions
        SET net_id = (
            SELECT net_seasons.net_id
            FROM net_seasons
            WHERE net_seasons.id = net_sessions.season_id
        )
        WHERE season_id IS NOT NULL
        """
    )

    # 3. Orphans (REAL_EVENT) go to Default Net. The multi-net migration
    #    asserts net id=1 exists; bail loudly if that invariant is broken.
    default_net_id = bind.execute(
        sa.text("SELECT id FROM nets ORDER BY id ASC LIMIT 1")
    ).scalar()
    if default_net_id is None:
        raise RuntimeError(
            "net_sessions backfill: no nets row exists; the multi-net migration "
            "must run first to create the Default Net."
        )
    op.execute(
        sa.text("UPDATE net_sessions SET net_id = :nid WHERE net_id IS NULL").bindparams(
            nid=default_net_id
        )
    )

    # 4. Now enforce NOT NULL + FK + index.
    with op.batch_alter_table("net_sessions") as batch:
        batch.alter_column("net_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_net_sessions_net_id_nets", "nets", ["net_id"], ["id"]
        )
        batch.create_index("ix_net_sessions_net_id", ["net_id"])


def downgrade() -> None:
    raise NotImplementedError("pre-alpha; no rollback")
