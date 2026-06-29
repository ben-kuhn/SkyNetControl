"""heal stale UNIQUE(name) constraints on per-net templates/tags

Revision ID: 7e2d4f81b3a9
Revises: 3c8e5fa10001
Create Date: 2026-06-29 16:00:00

The multi-net cutover migration (859ed3de034e) tried to drop the old
single-column ``UNIQUE(name)`` on roster_templates / reminder_templates /
activity_tags via ``op.batch_alter_table(recreate="always")``, on the
theory that SQLAlchemy's table rebuild silently drops anonymous
constraints. On at least one production DB that didn't actually happen —
the stale ``UNIQUE(name)`` survived alongside the new
``uq_<tbl>_net_name UNIQUE(net_id, name)``, blocking every second net
from getting its default templates seeded ("UNIQUE constraint failed:
roster_templates.name" on create_net).

This migration heals that state by rebuilding any affected table
explicitly. It detects the bad constraint in the stored DDL and is a
no-op on DBs that the cutover successfully cleaned up.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7e2d4f81b3a9"
down_revision: Union[str, None] = "3c8e5fa10001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Each entry: (table_name, ordered column list for the rebuild copy,
# DDL fragment for the new table body — minus the surrounding CREATE TABLE).
# The DDL must match the post-cutover shape so we don't drop any column the
# cutover added.
_TABLES = {
    "roster_templates": {
        "columns": [
            "id", "name", "subject_template", "header_template", "welcome_template",
            "comments_template", "footer_template", "lead_time_days", "is_default", "net_id",
        ],
        "body": """
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            subject_template TEXT NOT NULL,
            header_template TEXT NOT NULL,
            welcome_template TEXT NOT NULL,
            comments_template TEXT NOT NULL,
            footer_template TEXT NOT NULL,
            lead_time_days INTEGER NOT NULL,
            is_default BOOLEAN NOT NULL,
            net_id INTEGER NOT NULL,
            CONSTRAINT uq_roster_templates_net_name UNIQUE (net_id, name),
            FOREIGN KEY(net_id) REFERENCES nets(id)
        """,
    },
    "reminder_templates": {
        "columns": [
            "id", "name", "template_type", "subject_template", "body_template",
            "lead_time_days", "is_default", "net_id",
        ],
        "body": """
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            template_type VARCHAR(15) NOT NULL,
            subject_template TEXT NOT NULL,
            body_template TEXT NOT NULL,
            lead_time_days INTEGER NOT NULL,
            is_default BOOLEAN NOT NULL,
            net_id INTEGER NOT NULL,
            CONSTRAINT uq_reminder_templates_net_name UNIQUE (net_id, name),
            FOREIGN KEY(net_id) REFERENCES nets(id)
        """,
    },
    "activity_tags": {
        "columns": ["id", "name", "net_id"],
        "body": """
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            net_id INTEGER NOT NULL,
            CONSTRAINT uq_activity_tags_net_name UNIQUE (net_id, name),
            FOREIGN KEY(net_id) REFERENCES nets(id)
        """,
    },
}


def _sqlite_needs_heal(bind, tbl: str) -> bool:
    """Return True if the table's stored DDL still has the stale UNIQUE(name)."""
    ddl = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name = :tbl"),
        {"tbl": tbl},
    ).scalar()
    if not ddl:
        return False
    # Match either "UNIQUE (name)" or "UNIQUE(name)" — both forms appear in the wild.
    normalised = ddl.replace(" ", "")
    return "UNIQUE(name)" in normalised and "(net_id,name)" in normalised


def _heal_sqlite(bind, tbl: str, columns: list, body: str) -> None:
    cols_csv = ",".join(columns)
    bind.execute(sa.text(f"CREATE TABLE {tbl}_new ({body})"))  # noqa: S608
    bind.execute(sa.text(f"INSERT INTO {tbl}_new ({cols_csv}) SELECT {cols_csv} FROM {tbl}"))  # noqa: S608
    bind.execute(sa.text(f"DROP TABLE {tbl}"))
    bind.execute(sa.text(f"ALTER TABLE {tbl}_new RENAME TO {tbl}"))


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "sqlite":
        # Toggling foreign_keys is a no-op inside the alembic transaction on most
        # SQLAlchemy versions, but we still want to be explicit so any future
        # alembic env that enables FKs doesn't break the DROP/RENAME dance.
        bind.execute(sa.text("PRAGMA foreign_keys=OFF"))
        try:
            for tbl, spec in _TABLES.items():
                if _sqlite_needs_heal(bind, tbl):
                    _heal_sqlite(bind, tbl, spec["columns"], spec["body"])
        finally:
            bind.execute(sa.text("PRAGMA foreign_keys=ON"))
    else:
        # Postgres / other backends: the cutover used SQLAlchemy's auto-named
        # unique constraint (no name arg), which Postgres stores as
        # "<tbl>_name_key". Drop it if present; ignore if not.
        for tbl in _TABLES:
            constraint = f"{tbl}_name_key"
            try:
                op.drop_constraint(constraint, tbl, type_="unique")
            except Exception:
                # No matching constraint — already healed or never had it.
                pass


def downgrade() -> None:
    # Re-adding a globally-unique name across nets would break any DB that has
    # legitimately duplicated names per-net. Heal-forward only.
    raise NotImplementedError("heal-forward only")
