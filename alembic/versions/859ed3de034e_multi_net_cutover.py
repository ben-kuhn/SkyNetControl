"""multi net cutover

Revision ID: 859ed3de034e
Revises: c72c8361ac50
Create Date: 2026-06-23 15:44:47.317507

"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "859ed3de034e"
down_revision: Union[str, None] = "c72c8361ac50"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slugify(s: str) -> str:
    s = s.lower()
    out = []
    last_hyphen = False
    for c in s:
        if c.isalnum():
            out.append(c)
            last_hyphen = False
        elif not last_hyphen:
            out.append("-")
            last_hyphen = True
    return "".join(out)[:64].strip("-")


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create new tables: nets, net_memberships, net_config
    # -------------------------------------------------------------------------
    op.create_table(
        "nets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "net_memberships",
        sa.Column("user_callsign", sa.String(20), sa.ForeignKey("users.callsign"), primary_key=True),
        sa.Column("net_id", sa.Integer, sa.ForeignKey("nets.id"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "net_config",
        sa.Column("net_id", sa.Integer, sa.ForeignKey("nets.id"), primary_key=True),
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # -------------------------------------------------------------------------
    # 2. Insert default net (id=1)
    # -------------------------------------------------------------------------
    bind = op.get_bind()
    net_address = bind.execute(
        sa.text("SELECT value FROM app_config WHERE key = 'net_address'")
    ).scalar()
    name = net_address or "Default Net"
    slug = _slugify(name) or "default"
    now = datetime.now(timezone.utc).isoformat()
    bind.execute(
        sa.text(
            "INSERT INTO nets (id, slug, name, is_public, created_at) "
            "VALUES (1, :slug, :name, 1, :now)"
        ),
        {"slug": slug, "name": name, "now": now},
    )

    # -------------------------------------------------------------------------
    # 3 + 4. Add net_id to per-net tables, backfill, make NOT NULL.
    #
    # IMPORTANT SQLite/Alembic quirk: using batch_alter_table with recreate="always"
    # to add an FK column creates an anonymous FK constraint in the reflected metadata.
    # A subsequent batch pass then fails with "Constraint must have a name" when it
    # tries to process those anonymous constraints.
    #
    # Workaround: use raw "ALTER TABLE … ADD COLUMN" (which SQLite supports natively)
    # for the initial nullable add, then use a SINGLE batch_alter_table(recreate="always")
    # to make the column NOT NULL (and optionally replace unique constraints) in one pass.
    # -------------------------------------------------------------------------

    # Simple per-net tables with no UNIQUE(name) constraint to replace:
    for tbl in ("net_seasons", "activities"):
        op.execute(f"ALTER TABLE {tbl} ADD COLUMN net_id INTEGER REFERENCES nets(id)")  # noqa: S608
        op.execute(f"UPDATE {tbl} SET net_id = 1")  # noqa: S608
        with op.batch_alter_table(tbl, recreate="always") as batch:
            batch.alter_column("net_id", nullable=False)

    # Tables with UNIQUE(name) that must become UNIQUE(net_id, name).
    # The old unnamed UNIQUE(name) constraint is silently dropped by recreate="always"
    # (since Alembic skips unnamed constraints when rebuilding), and we add the new
    # named one explicitly.
    for tbl in ("roster_templates", "reminder_templates", "activity_tags"):
        op.execute(f"ALTER TABLE {tbl} ADD COLUMN net_id INTEGER REFERENCES nets(id)")  # noqa: S608
        op.execute(f"UPDATE {tbl} SET net_id = 1")  # noqa: S608
        with op.batch_alter_table(tbl, recreate="always") as batch:
            batch.alter_column("net_id", nullable=False)
            batch.create_unique_constraint(f"uq_{tbl}_net_name", ["net_id", "name"])

    # -------------------------------------------------------------------------
    # 5. members PK: (callsign) → (net_id, callsign)
    # -------------------------------------------------------------------------
    op.execute("ALTER TABLE members ADD COLUMN net_id INTEGER REFERENCES nets(id)")
    op.execute("UPDATE members SET net_id = 1")
    with op.batch_alter_table("members", recreate="always") as batch:
        batch.alter_column("net_id", nullable=False)
        batch.create_primary_key("pk_members", ["net_id", "callsign"])

    # -------------------------------------------------------------------------
    # 6. Backfill net_memberships from users.role
    #    Only net_control and viewer become net members; admin/pending/deleted do not.
    # -------------------------------------------------------------------------
    bind.execute(
        sa.text(
            f"INSERT INTO net_memberships (user_callsign, net_id, role, created_at) "
            f"SELECT callsign, 1, role, '{now}' "
            f"FROM users "
            f"WHERE role IN ('net_control', 'viewer')"
        )
    )

    # -------------------------------------------------------------------------
    # 7. Add is_pending and is_deleted to users, backfill from role, drop role.
    #    (Task 4 amendment: is_pending + is_deleted added here alongside role drop.)
    # -------------------------------------------------------------------------
    with op.batch_alter_table("users", recreate="always") as batch:
        batch.add_column(
            sa.Column("is_pending", sa.Boolean, nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("is_deleted", sa.Boolean, nullable=False, server_default="0")
        )
    op.execute("UPDATE users SET is_pending = 1 WHERE role = 'pending'")
    op.execute("UPDATE users SET is_deleted = 1 WHERE role = 'deleted'")
    with op.batch_alter_table("users", recreate="always") as batch:
        batch.drop_column("role")

    # -------------------------------------------------------------------------
    # 8. personal_access_tokens.net_id
    #    Admin-only scopes (users:*, config:*, nets:*) → NULL
    #    Everything else → net 1
    # -------------------------------------------------------------------------
    op.execute("ALTER TABLE personal_access_tokens ADD COLUMN net_id INTEGER REFERENCES nets(id)")
    op.execute(
        "UPDATE personal_access_tokens "
        "SET net_id = 1 "
        "WHERE NOT ("
        "    scopes LIKE '%users:%' OR scopes LIKE '%config:%' OR scopes LIKE '%nets:%'"
        ")"
    )

    # -------------------------------------------------------------------------
    # 9. audit_log.net_id — best-effort backfill; column nullable forever.
    # -------------------------------------------------------------------------
    op.execute("ALTER TABLE audit_log ADD COLUMN net_id INTEGER")
    op.execute(
        "UPDATE audit_log SET net_id = 1 "
        "WHERE action LIKE 'schedule.%' "
        "   OR action LIKE 'checkin%' "
        "   OR action LIKE 'roster.%' "
        "   OR action LIKE 'reminder.%' "
        "   OR action LIKE 'activity.%'"
    )

    # -------------------------------------------------------------------------
    # 10. Move per-net app_config keys into net_config, then delete from app_config
    # -------------------------------------------------------------------------
    PER_NET_PREFIXES = (
        "net_address",
        "default_net_control",
        "pat_mailbox_path",
        "scanner.",
        "delivery.",
        "callbook.",
    )
    rows = bind.execute(sa.text("SELECT key, value, updated_at FROM app_config")).fetchall()
    for key, value, updated_at in rows:
        if any(key == p or (p.endswith(".") and key.startswith(p)) for p in PER_NET_PREFIXES):
            bind.execute(
                sa.text(
                    "INSERT INTO net_config (net_id, key, value, updated_at) "
                    "VALUES (1, :k, :v, :u)"
                ),
                {"k": key, "v": value, "u": updated_at},
            )
            bind.execute(sa.text("DELETE FROM app_config WHERE key = :k"), {"k": key})


def downgrade() -> None:
    raise NotImplementedError("multi-net cutover is one-way (pre-alpha)")
