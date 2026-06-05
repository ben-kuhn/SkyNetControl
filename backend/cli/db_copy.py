"""Copy every row from one SkyNetControl database URL to another.

The target URL must already be migrated to the same revision as the
source (`skynetcontrol-alembic upgrade head` against it first). The
copy uses SQLAlchemy core in dependency order so foreign keys land
intact, and resets PostgreSQL sequences after so future inserts don't
collide with copied IDs.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine

from backend.db.base import Base
# Import every package that defines models so Base.metadata is fully
# populated before we read sorted_tables. (Each of these imports has
# the side effect of registering its Base subclasses.)
import backend.auth.models  # noqa: F401
import backend.auth.pat_models  # noqa: F401
import backend.audit.models  # noqa: F401
import backend.config_mgmt.models  # noqa: F401
import backend.integrations.callbook.models  # noqa: F401
import backend.integrations.delivery.models  # noqa: F401
import backend.modules.activities.models  # noqa: F401
import backend.modules.checkins.models  # noqa: F401
import backend.modules.notifications.models  # noqa: F401
import backend.modules.reminders.models  # noqa: F401
import backend.modules.roster.models  # noqa: F401
import backend.modules.schedule.models  # noqa: F401


_BATCH_SIZE = 1000


def _assert_target_ready(target: Engine) -> None:
    inspector = inspect(target)
    existing = set(inspector.get_table_names())
    expected = {t.name for t in Base.metadata.sorted_tables}
    missing = expected - existing
    if missing:
        raise RuntimeError(
            f"target is missing tables: {sorted(missing)}. "
            "Run `skynetcontrol-alembic upgrade head` against the target first."
        )

    with target.connect() as conn:
        for table in Base.metadata.sorted_tables:
            count = conn.execute(select(text("COUNT(*)")).select_from(table)).scalar()
            if count:
                raise RuntimeError(
                    f"target is not empty: table {table.name!r} has {count} row(s). "
                    "Refusing to copy into a populated database."
                )


def _reset_postgres_sequences(target: Engine) -> None:
    """After bulk insert, advance each PG sequence past the max copied id."""
    if target.dialect.name != "postgresql":
        return
    with target.begin() as conn:
        for table in Base.metadata.sorted_tables:
            for column in table.columns:
                if column.autoincrement and column.primary_key:
                    try:
                        if column.type.python_type is not int:
                            continue
                    except NotImplementedError:
                        continue
                    conn.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence(:tname, :cname), "
                            f"COALESCE((SELECT MAX({column.name}) FROM {table.name}), 0) + 1, false)"
                        ),
                        {"tname": table.name, "cname": column.name},
                    )


def copy_database(source_url: str, target_url: str) -> dict[str, int]:
    """Copy every row from source DB to target DB. Returns row counts per table.

    Raises RuntimeError if target is unmigrated or already has data.
    """
    source = create_engine(source_url)
    target = create_engine(target_url)
    try:
        _assert_target_ready(target)

        counts: dict[str, int] = {}
        with source.connect() as src_conn, target.begin() as dst_conn:
            for table in Base.metadata.sorted_tables:
                rows = src_conn.execute(select(table)).mappings().all()
                if not rows:
                    counts[table.name] = 0
                    continue
                # Insert in batches to keep memory usage bounded on big tables.
                for offset in range(0, len(rows), _BATCH_SIZE):
                    chunk = [dict(r) for r in rows[offset : offset + _BATCH_SIZE]]
                    dst_conn.execute(table.insert(), chunk)
                counts[table.name] = len(rows)

        _reset_postgres_sequences(target)
        return counts
    finally:
        source.dispose()
        target.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skynetcontrol-db-copy",
        description="Copy a SkyNetControl database from one SQLAlchemy URL to another.",
        epilog=(
            "The target URL must already be migrated to head and empty. "
            "Typical use: skynetcontrol-alembic upgrade head against the target "
            "DB first, then this command."
        ),
    )
    parser.add_argument("source_url", help="SQLAlchemy URL of the source database")
    parser.add_argument("target_url", help="SQLAlchemy URL of the migrated, empty target database")
    args = parser.parse_args(argv)

    try:
        counts = copy_database(args.source_url, args.target_url)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    total = sum(counts.values())
    nonempty = {t: n for t, n in counts.items() if n}
    print(f"Copied {total} row(s) across {len(nonempty)} table(s):")
    for t, n in sorted(nonempty.items(), key=lambda kv: -kv[1]):
        print(f"  {t}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
