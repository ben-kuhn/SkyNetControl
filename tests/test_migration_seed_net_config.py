"""Tests for the migration that seeds net_config from app_config."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, String, Text, create_engine, MetaData, Table, ForeignKey, DateTime
from sqlalchemy.engine import Engine

MOVED_KEYS = [
    "default_net_control",
    "net_address",
    "pat_mailbox_path",
    "scanner.enabled",
    "scanner.interval_minutes",
    "delivery.backends",
    "delivery.email.to_address",
    "delivery.groupsio.group_name",
    "delivery.winlink.target_address",
]


def _find_migration_module():
    versions_dir = Path(__file__).parent.parent / "alembic" / "versions"
    candidates = list(versions_dir.glob("*_seed_net_config_from_app_config*.py"))
    assert len(candidates) == 1, f"Expected one matching migration, got {candidates}"
    spec = importlib.util.spec_from_file_location("seed_net_config_migration", candidates[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_pre_migration_schema(engine: Engine):
    """Build just enough schema to test the migration's data movement."""
    metadata = MetaData()
    Table(
        "app_config",
        metadata,
        Column("key", String, primary_key=True),
        Column("value", Text, nullable=False),
        Column("updated_at", DateTime),
    )
    Table(
        "nets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("slug", String, nullable=False),
        Column("name", String, nullable=False),
    )
    Table(
        "net_config",
        metadata,
        Column("net_id", Integer, ForeignKey("nets.id"), primary_key=True),
        Column("key", String, primary_key=True),
        Column("value", Text, nullable=False),
        Column("updated_at", DateTime),
    )
    metadata.create_all(engine)
    return metadata


@pytest.fixture
def engine():
    return create_engine("sqlite://")


def _run_upgrade(engine: Engine):
    """Invoke the migration's upgrade() against an alembic-style context."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = _find_migration_module()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()


def test_upgrade_seeds_net_config_for_missing_keys(engine):
    md = _build_pre_migration_schema(engine)
    with engine.begin() as c:
        c.execute(md.tables["nets"].insert(), [
            {"id": 1, "slug": "alpha", "name": "Alpha"},
            {"id": 2, "slug": "beta", "name": "Beta"},
        ])
        c.execute(md.tables["app_config"].insert(), [
            {"key": k, "value": f"global-{k}"} for k in MOVED_KEYS
        ])

    _run_upgrade(engine)

    with engine.begin() as c:
        rows = list(c.execute(md.tables["net_config"].select()))
        app_rows = list(c.execute(md.tables["app_config"].select()))

    by_net: dict[int, dict[str, str]] = {1: {}, 2: {}}
    for r in rows:
        by_net[r.net_id][r.key] = r.value
    # Each net got all moved keys seeded + winlink_enabled
    for net_id in (1, 2):
        for k in MOVED_KEYS:
            assert by_net[net_id][k] == f"global-{k}", f"net {net_id} missing {k}"
        assert by_net[net_id]["winlink_enabled"] == "true"
    # All moved keys removed from app_config
    app_keys = {r.key for r in app_rows}
    assert app_keys.isdisjoint(set(MOVED_KEYS))


def test_upgrade_preserves_existing_net_config_values(engine):
    md = _build_pre_migration_schema(engine)
    with engine.begin() as c:
        c.execute(md.tables["nets"].insert(), [{"id": 1, "slug": "alpha", "name": "Alpha"}])
        c.execute(md.tables["app_config"].insert(), [
            {"key": "default_net_control", "value": "global-call"},
        ])
        # Net already has a value — must not be overwritten
        c.execute(md.tables["net_config"].insert(), [
            {"net_id": 1, "key": "default_net_control", "value": "net-specific-call"},
        ])

    _run_upgrade(engine)

    with engine.begin() as c:
        rows = list(c.execute(md.tables["net_config"].select().where(
            md.tables["net_config"].c.net_id == 1
        )))
    by_key = {r.key: r.value for r in rows}
    assert by_key["default_net_control"] == "net-specific-call"


def test_upgrade_leaves_unrelated_app_config_keys(engine):
    md = _build_pre_migration_schema(engine)
    with engine.begin() as c:
        c.execute(md.tables["nets"].insert(), [{"id": 1, "slug": "alpha", "name": "Alpha"}])
        c.execute(md.tables["app_config"].insert(), [
            {"key": "claude_api_key", "value": "sk-keep"},
            {"key": "callbook.providers", "value": '["hamqth"]'},
            {"key": "default_net_control", "value": "W0NE"},
        ])

    _run_upgrade(engine)

    with engine.begin() as c:
        rows = {r.key: r.value for r in c.execute(md.tables["app_config"].select())}
    assert rows.get("claude_api_key") == "sk-keep"
    assert rows.get("callbook.providers") == '["hamqth"]'
    assert "default_net_control" not in rows


def test_upgrade_with_no_app_config_rows_still_sets_winlink_enabled(engine):
    md = _build_pre_migration_schema(engine)
    with engine.begin() as c:
        c.execute(md.tables["nets"].insert(), [{"id": 1, "slug": "alpha", "name": "Alpha"}])

    _run_upgrade(engine)

    with engine.begin() as c:
        rows = list(c.execute(md.tables["net_config"].select()))
    assert any(r.key == "winlink_enabled" and r.value == "true" and r.net_id == 1 for r in rows)
    # Nothing else seeded — no app_config to copy from
    assert all(r.key == "winlink_enabled" for r in rows)
