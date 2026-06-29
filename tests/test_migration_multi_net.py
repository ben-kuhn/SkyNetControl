"""Tests for the multi-net cutover migration (revision 859ed3de034e).

These tests run Alembic against a temporary SQLite database so they don't
touch the dev DB.
"""
import os
import pathlib
import sqlite3
import subprocess


def test_migration_creates_default_net(tmp_path):
    """On a fresh DB, upgrading to head creates a single Default Net row."""
    db = tmp_path / "t.db"
    env = os.environ.copy()
    env["SKYNET_DATABASE_URL"] = f"sqlite:///{db}"
    r = subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        cwd=pathlib.Path.cwd(),
    )
    assert r.returncode == 0, r.stderr

    con = sqlite3.connect(str(db))
    rows = con.execute("SELECT id, slug, name, is_public FROM nets").fetchall()
    # slug is _slugify("Default Net") = "default-net" (spaces become hyphens)
    assert rows == [(1, "default-net", "Default Net", 1)]

    # users table must not have a 'role' column
    col_names = [r[1] for r in con.execute("PRAGMA table_info(users)")]
    assert "role" not in col_names
    assert "is_pending" in col_names
    assert "is_deleted" in col_names

    # members PK is (net_id, callsign)
    pk = con.execute("PRAGMA table_info(members)").fetchall()
    pk_cols = [r[1] for r in pk if r[5] > 0]  # r[5] is pk index (1-based, 0 means not pk)
    assert set(pk_cols) == {"net_id", "callsign"}


def test_migration_backfills_existing_data(tmp_path):
    """Seed the DB at the bridge revision, insert sample rows, upgrade, assert backfill."""
    db = tmp_path / "t.db"
    env = os.environ.copy()
    env["SKYNET_DATABASE_URL"] = f"sqlite:///{db}"

    # Upgrade to the revision immediately before multi-net cutover (Task 2 head).
    bridge_rev = "c72c8361ac50"
    subprocess.run(
        [".venv/bin/alembic", "upgrade", bridge_rev],
        env=env,
        check=True,
        cwd=pathlib.Path.cwd(),
    )

    con = sqlite3.connect(str(db))
    # Insert a per-net app_config key that should be moved to net_config
    con.execute(
        "INSERT INTO app_config (key, value, updated_at) "
        "VALUES ('net_address', 'w0ne@winlink.org', '2026-01-01')"
    )
    # Insert a net_season row (columns at bridge revision)
    con.execute(
        "INSERT INTO net_seasons (id, name, start_date, end_date, is_week_long, activity_cadence) "
        "VALUES (1, 'S1', '2026-01-01', '2026-12-31', 0, 2)"
    )
    # Insert a user with net_control role to verify net_memberships backfill
    con.execute(
        "INSERT INTO users (callsign, oidc_subject, name, role, created_at) "
        "VALUES ('W0NE', 'sub-1', 'Test User', 'net_control', '2026-01-01T00:00:00')"
    )
    # Insert a user with pending role to verify is_pending backfill
    con.execute(
        "INSERT INTO users (callsign, oidc_subject, name, role, created_at) "
        "VALUES ('K0NE', 'sub-2', 'Pending User', 'pending', '2026-01-01T00:00:00')"
    )
    con.commit()
    con.close()

    subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"],
        env=env,
        check=True,
        cwd=pathlib.Path.cwd(),
    )

    con = sqlite3.connect(str(db))

    # net_seasons got net_id=1
    assert con.execute("SELECT net_id FROM net_seasons").fetchone() == (1,)

    # Default Net got slug derived from 'w0ne@winlink.org'
    assert con.execute("SELECT slug FROM nets").fetchone() == ("w0ne-winlink-org",)

    # net_address moved from app_config to net_config
    assert con.execute(
        "SELECT value FROM net_config WHERE key='net_address'"
    ).fetchone() == ("w0ne@winlink.org",)
    assert con.execute(
        "SELECT COUNT(*) FROM app_config WHERE key='net_address'"
    ).fetchone() == (0,)

    # net_control user got a net_memberships row
    nm = con.execute(
        "SELECT net_id, role FROM net_memberships WHERE user_callsign='W0NE'"
    ).fetchone()
    assert nm == (1, "net_control")

    # pending user got is_pending=1, no membership row
    row = con.execute(
        "SELECT is_pending, is_deleted FROM users WHERE callsign='K0NE'"
    ).fetchone()
    assert row == (1, 0)
    assert con.execute(
        "SELECT COUNT(*) FROM net_memberships WHERE user_callsign='K0NE'"
    ).fetchone() == (0,)


def test_add_net_id_to_net_sessions_is_idempotent(tmp_path):
    """Partial-failure recovery for migration 3c8e5fa10001.

    SQLite's non-transactional DDL means a previous deploy can succeed on
    `ADD COLUMN net_id` but fail on a later step (backfill / NOT NULL /
    FK / index), leaving the column NULL with no FK/index and alembic
    still stamped at the parent revision. The fix simulated here: upgrade
    to the parent revision, hand-add the column to mimic the broken state,
    then upgrade to head and verify the migration recovers cleanly.
    """
    db = tmp_path / "t.db"
    env = os.environ.copy()
    env["SKYNET_DATABASE_URL"] = f"sqlite:///{db}"
    parent_rev = "a5b2c7e91a04"

    subprocess.run(
        [".venv/bin/alembic", "upgrade", parent_rev],
        env=env,
        check=True,
        cwd=pathlib.Path.cwd(),
    )

    con = sqlite3.connect(str(db))
    # Seed a season-bound session AND an orphan REAL_EVENT session so both
    # backfill branches get exercised on retry.
    con.execute(
        "INSERT INTO net_seasons (id, net_id, name, start_date, end_date, is_week_long, activity_cadence) "
        "VALUES (5, 1, 'S1', '2026-01-01', '2026-12-31', 0, 2)"
    )
    con.execute(
        "INSERT INTO net_sessions (id, season_id, start_date, grace_period_hours, session_type, status) "
        "VALUES (10, 5, '2026-03-01', 12, 'NET', 'COMPLETED')"
    )
    con.execute(
        "INSERT INTO net_sessions (id, season_id, start_date, grace_period_hours, session_type, status) "
        "VALUES (11, NULL, '2026-03-15', 12, 'REAL_EVENT', 'COMPLETED')"
    )
    # Simulate the partial-failure state: column added but never populated,
    # no FK, no index. Alembic version still at parent.
    con.execute("ALTER TABLE net_sessions ADD COLUMN net_id INTEGER")
    con.commit()
    con.close()

    r = subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        cwd=pathlib.Path.cwd(),
    )
    assert r.returncode == 0, r.stderr

    con = sqlite3.connect(str(db))
    # Season-bound session backfilled from season.net_id; orphan got default net.
    assert con.execute("SELECT net_id FROM net_sessions WHERE id=10").fetchone() == (1,)
    assert con.execute("SELECT net_id FROM net_sessions WHERE id=11").fetchone() == (1,)
    # net_id is now NOT NULL, has an FK to nets, and the index was created.
    cols = {r[1]: r[3] for r in con.execute("PRAGMA table_info(net_sessions)")}
    assert cols["net_id"] == 1, "net_id must be NOT NULL after recovery"
    fk_targets = {r[2] for r in con.execute("PRAGMA foreign_key_list(net_sessions)")}
    assert "nets" in fk_targets
    indexes = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='net_sessions'"
    )}
    assert "ix_net_sessions_net_id" in indexes
