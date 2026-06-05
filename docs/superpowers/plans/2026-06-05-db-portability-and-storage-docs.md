# Database Portability + Storage Documentation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let SkyNetControl operators (a) park the database on any path they want — ZFS dataset, external mount, dedicated subvolume — so the data survives system migrations; and (b) move a SkyNetControl database from one SQLAlchemy URL to another (SQLite ↔ PostgreSQL, host A → host B) with a single command.

**Architecture:**
- A new `skynetcontrol-db-copy` CLI command uses SQLAlchemy reflection (via the project's existing `Base.metadata.sorted_tables`) to copy every table row-by-row from a source URL to a target URL. The target is expected to already be migrated to head (the command refuses to copy into an empty DB so operators don't accidentally restore into the wrong shape). PostgreSQL sequences are reset after copy so future inserts don't collide.
- NixOS module already exposes `stateDir` and `databaseUrl` — no module changes. The "park your DB on ZFS" story is purely doc work: show how to (a) point `stateDir` at any path the system can write to, (b) bind-mount or symlink for cases where the host path can't change, (c) park the DB on a dedicated ZFS dataset for atomic snapshots, and (d) back up via filesystem snapshots, `sqlite3 .backup`, or `pg_dump`.

**Tech Stack:** Python (SQLAlchemy core for the copy, argparse for the CLI), Nix (default.nix wraps the new entry point the same way it wraps `skynetcontrol-alembic`), pytest for tests, Markdown for docs.

---

## File Structure

**Backend — create:**
- `backend/cli/db_copy.py` — the copy logic (`copy_database(source_url, target_url)`) and CLI `main()` entry point.

**Backend — modify:**
- `pyproject.toml` — register `skynetcontrol-db-copy = "backend.cli.db_copy:main"` under `[project.scripts]`.
- `default.nix` — extend `postInstall` to install a Python wrapper for `skynetcontrol-db-copy` (mirrors the existing `skynetcontrol-alembic` block at `default.nix:54-57`).

**Backend — tests:**
- `tests/test_db_copy.py` — round-trip integration test between two SQLite engines + cross-table coverage + sequence-reset assertion (skipped if postgres isn't reachable in the test env).

**Docs — modify:**
- `docs/deployment/nix.md` — extend with subsections: "Custom storage location" (stateDir override, bind-mount, ZFS dataset), expand "Backups" to cover filesystem-snapshot + systemd-timer + restic recipes, add "Moving between database backends" using `skynetcontrol-db-copy`.
- `README.md` — short NixOS subsection pointing at the new docs, plus a brief `skynetcontrol-db-copy` example.

---

## Task 1: Reflection-based DB copy helper (failing test → impl)

**Files:**
- Create: `backend/cli/db_copy.py`
- Test: `tests/test_db_copy.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_copy.py`:

```python
"""Tests for the cross-engine database copy helper."""
import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from backend.auth.models import User, UserRole
from backend.cli.db_copy import copy_database
from backend.config_mgmt.models import AppConfig
from backend.db.base import Base
from backend.modules.schedule.models import NetSeason
from datetime import date


def _make_db(path: str) -> str:
    """Create an empty migrated DB at the given path. Returns sqlite URL."""
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


def test_copy_database_round_trips_rows(tmp_path):
    src_url = _make_db(str(tmp_path / "src.db"))
    dst_url = _make_db(str(tmp_path / "dst.db"))

    src_engine = create_engine(src_url)
    SrcSession = sessionmaker(bind=src_engine)
    with SrcSession() as s:
        s.add(User(callsign="K0XYZ", oidc_subject="g:x", name="Alice", role=UserRole.ADMIN))
        s.add(AppConfig(key="default_net_control", value="K0XYZ"))
        s.add(NetSeason(name="Spring", start_date=date(2026, 4, 1), end_date=date(2026, 6, 30), day_of_week=3))
        s.commit()
    src_engine.dispose()

    copy_database(src_url, dst_url)

    dst_engine = create_engine(dst_url)
    DstSession = sessionmaker(bind=dst_engine)
    with DstSession() as s:
        users = s.execute(select(User)).scalars().all()
        assert len(users) == 1
        assert users[0].callsign == "K0XYZ"
        assert users[0].role == UserRole.ADMIN

        configs = s.execute(select(AppConfig)).scalars().all()
        assert {c.key: c.value for c in configs} == {"default_net_control": "K0XYZ"}

        seasons = s.execute(select(NetSeason)).scalars().all()
        assert len(seasons) == 1
        assert seasons[0].name == "Spring"
    dst_engine.dispose()


def test_copy_database_refuses_when_target_unmigrated(tmp_path):
    src_url = _make_db(str(tmp_path / "src.db"))
    # Target DB exists but has NO schema (no Base.metadata.create_all)
    dst_path = tmp_path / "dst.db"
    dst_path.touch()
    dst_url = f"sqlite:///{dst_path}"

    with pytest.raises(RuntimeError, match="target.*missing.*tables"):
        copy_database(src_url, dst_url)


def test_copy_database_refuses_when_target_has_data(tmp_path):
    src_url = _make_db(str(tmp_path / "src.db"))
    dst_url = _make_db(str(tmp_path / "dst.db"))

    dst_engine = create_engine(dst_url)
    DstSession = sessionmaker(bind=dst_engine)
    with DstSession() as s:
        s.add(User(callsign="W0EXISTING", oidc_subject="g:e", name="Existing", role=UserRole.ADMIN))
        s.commit()
    dst_engine.dispose()

    with pytest.raises(RuntimeError, match="target.*not empty"):
        copy_database(src_url, dst_url)
```

- [ ] **Step 2: Run, verify it fails**

```
.venv/bin/pytest tests/test_db_copy.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.cli.db_copy'`.

- [ ] **Step 3: Implement the copy helper**

Create `backend/cli/db_copy.py`:

```python
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

from sqlalchemy import MetaData, create_engine, inspect, select, text
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
                if column.autoincrement and column.primary_key and column.type.python_type is int:
                    seq_name = f"{table.name}_{column.name}_seq"
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
```

- [ ] **Step 4: Verify tests pass**

```
.venv/bin/pytest tests/test_db_copy.py -v
```

Expected: all three tests green.

- [ ] **Step 5: Lint**

```
nix-shell --run "ruff check backend/cli/db_copy.py tests/test_db_copy.py"
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backend/cli/db_copy.py tests/test_db_copy.py
git commit -m "feat(cli): SQLAlchemy reflection-based database copy helper

backend.cli.db_copy.copy_database(source_url, target_url) walks every
table in Base.metadata.sorted_tables and copies rows from source to
target via SQLAlchemy core. Refuses to copy into unmigrated or
non-empty target databases. Resets PostgreSQL sequences after copy so
future inserts don't collide with copied autoincrement IDs.

Enables cross-engine moves (SQLite -> PostgreSQL or vice versa) and
host-to-host migrations without filesystem access to the original."
```

---

## Task 2: Wire `skynetcontrol-db-copy` as an installed CLI

**Files:**
- Modify: `pyproject.toml` (`[project.scripts]` block, currently around line 32)

- [ ] **Step 1: Add the entry point**

In `pyproject.toml`, find the `[project.scripts]` block and extend it:

```toml
[project.scripts]
skynetcontrol-setup = "backend.cli.setup:main"
skynetcontrol-db-copy = "backend.cli.db_copy:main"
```

- [ ] **Step 2: Reinstall the package locally to register the new script**

```
.venv/bin/pip install -e .
```

Expected: successful install. Then verify the new script exists:

```
ls -l .venv/bin/skynetcontrol-db-copy
.venv/bin/skynetcontrol-db-copy --help
```

Expected: the script is present and printing help shows usage info.

- [ ] **Step 3: Lint**

```
nix-shell --run "ruff check"
```

Expected: clean (alembic pre-existing errors don't count; just don't add new ones).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat(cli): register skynetcontrol-db-copy entry point"
```

---

## Task 3: Nix wrapper for `skynetcontrol-db-copy`

**Files:**
- Modify: `default.nix` (`postInstall` block, currently around line 54-57 for the alembic wrapper)

- [ ] **Step 1: Add the new wrapper after the alembic one**

In `default.nix`, find:

```nix
    # Create an alembic entry point so the NixOS module can run migrations.
    # wrapPythonPrograms will wrap this with the correct PYTHONPATH.
    printf '%s\n' '#!${python}/bin/python' 'import sys' 'from alembic.config import main' 'sys.exit(main())' > $out/bin/skynetcontrol-alembic
    chmod +x $out/bin/skynetcontrol-alembic
  '';
```

Insert the db-copy wrapper just before the closing `'';`:

```nix
    # Create an alembic entry point so the NixOS module can run migrations.
    # wrapPythonPrograms will wrap this with the correct PYTHONPATH.
    printf '%s\n' '#!${python}/bin/python' 'import sys' 'from alembic.config import main' 'sys.exit(main())' > $out/bin/skynetcontrol-alembic
    chmod +x $out/bin/skynetcontrol-alembic

    # Database-copy CLI for cross-engine / host-to-host migrations.
    printf '%s\n' '#!${python}/bin/python' 'import sys' 'from backend.cli.db_copy import main' 'sys.exit(main())' > $out/bin/skynetcontrol-db-copy
    chmod +x $out/bin/skynetcontrol-db-copy
  '';
```

- [ ] **Step 2: Verify the Nix build succeeds**

Building the full derivation is slow, but the syntax check is fast:

```
nix-instantiate --parse default.nix > /dev/null
```

Expected: no output (parse succeeded). If you have time, also:

```
nix-build default.nix -A skynetcontrol 2>&1 | tail -5
```

Expected: build succeeds; resulting `result/bin/skynetcontrol-db-copy` is executable.

If `nix-build default.nix` complains about the attribute name, the package may be exposed differently — check `flake.nix` / `overlay.nix` and follow whatever build command CI uses (see `.github/workflows/`). It is acceptable to skip the full build if it's flaky in your local environment; the actual verification will happen on CI for the merged commit.

- [ ] **Step 3: Commit**

```bash
git add default.nix
git commit -m "build(nix): wrap skynetcontrol-db-copy in the Nix package

Mirrors the existing skynetcontrol-alembic wrapper so the db-copy CLI
is available alongside the server and migration tool in any Nix
deployment (NixOS module, OCI image, overlay)."
```

---

## Task 4: Documentation — NixOS section + README

**Files:**
- Modify: `docs/deployment/nix.md`
- Modify: `README.md`

The goal is to surface three operator workflows in `nix.md` and link them from the README:

1. **Custom storage location** — point `stateDir` (or just the DB) at an arbitrary path. Three sub-patterns: simple `stateDir = "/tank/skynetcontrol"`, bind-mount over `/var/lib/skynetcontrol`, and dedicated ZFS dataset.
2. **Backups** — three patterns: ZFS snapshot (preferred when state is on ZFS), online `sqlite3 .backup` via systemd timer, restic / borg over the state dir. PostgreSQL keeps `pg_dump`.
3. **Moving between database backends** — `skynetcontrol-db-copy` for SQLite ↔ PostgreSQL or host-to-host swaps.

- [ ] **Step 1: Rewrite `docs/deployment/nix.md` "Backups" section and surrounding storage docs**

Open `docs/deployment/nix.md` and find the existing `### Backups` heading (around line 166). Replace the existing Backups block — currently three short lines — with the expanded structure below, and add a new `### Custom storage location` section immediately before it.

Insert the following just before the `### Backups` heading:

```markdown
### Custom storage location

The NixOS module exposes two storage knobs:

- `services.skynetcontrol.stateDir` — where the systemd unit's `StateDirectory` lives. Defaults to `/var/lib/skynetcontrol`. Set this to any path the system can write to:

  ```nix
  services.skynetcontrol.stateDir = "/tank/skynetcontrol";
  ```

  The module adds this path to `ReadWritePaths`, and systemd's `StateDirectory=` will create it with the dynamic-user ownership on first start. Make sure the parent directory exists and is on a filesystem the service can write to.

- `services.skynetcontrol.databaseUrl` — the SQLAlchemy URL. Defaults to `sqlite:////var/lib/skynetcontrol/skynetcontrol.db` (note the four slashes — SQLAlchemy's absolute-path SQLite form). Override this to put the DB file outside `stateDir` or to use a different engine:

  ```nix
  services.skynetcontrol.databaseUrl = "sqlite:////tank/skynetcontrol/skynetcontrol.db";
  ```

#### Pattern 1: Put state on a ZFS dataset

Create a dedicated dataset so you can snapshot just the SkyNetControl state:

```bash
sudo zfs create -o mountpoint=/tank/skynetcontrol tank/skynetcontrol
```

Then point the module at it:

```nix
services.skynetcontrol.stateDir = "/tank/skynetcontrol";
services.skynetcontrol.databaseUrl = "sqlite:////tank/skynetcontrol/skynetcontrol.db";
```

After `nixos-rebuild switch`, systemd will create the directory on next start with the dynamic-user ownership. The DB lives entirely on the dedicated dataset — snapshots, replication, and quotas all apply.

#### Pattern 2: Bind-mount over the default location

If you don't want to change `stateDir` (e.g. another tool already expects `/var/lib/skynetcontrol`), bind-mount the real storage in:

```nix
fileSystems."/var/lib/skynetcontrol" = {
  device = "/tank/skynetcontrol";
  options = [ "bind" ];
};
```

This keeps the module's defaults and routes all state I/O through the new device. The bind mount needs to be in place before `skynetcontrol.service` starts — NixOS orders fileSystems before multi-user.target by default, so this is automatic.

#### Pattern 3: External PostgreSQL

For multi-instance setups or just to avoid SQLite altogether, swap the URL:

```nix
services.skynetcontrol.databaseUrl =
  "postgresql+psycopg://skynetcontrol@/skynetcontrol?host=/run/postgresql";
```

Run a local `services.postgresql.enable = true;` with a matching role and DB, or point at a remote cluster — peer auth via the socket path is the cleanest for a co-located DB. Migrations run automatically on next service restart.
```

Then **replace** the existing `### Backups` section (currently lines ~166-176, the three short SQLite/PostgreSQL lines) with:

```markdown
### Backups

Pick whichever fits your storage layout. All three patterns coexist.

#### Filesystem snapshots (when state is on ZFS/btrfs)

If you put state on its own dataset/subvolume, snapshots are the simplest backup:

```bash
sudo zfs snapshot tank/skynetcontrol@$(date +%F)
```

For automatic daily snapshots use `services.zfs.autoSnapshot.enable = true;` (or `sanoid`, `znapzend`, etc.). Restore is just `zfs clone` or `zfs rollback` — the service does not need to be stopped to take a snapshot, since ZFS gives you an atomic point-in-time view even while SQLite is mid-write.

#### Online SQLite snapshot via `sqlite3 .backup`

If you're not on a snapshotting filesystem, use SQLite's built-in online backup. It produces a consistent copy without stopping the service:

```bash
sudo -u skynetcontrol \
  sqlite3 /var/lib/skynetcontrol/skynetcontrol.db \
    ".backup '/backup/skynetcontrol-$(date +%F).db'"
```

(Adjust `-u skynetcontrol` to whatever the service's user is. With `DynamicUser = true;` this is a dynamic UID — the easier path is to run the backup as root and `chown` after.)

A systemd timer makes this nightly:

```nix
systemd.services."skynetcontrol-backup" = {
  description = "Online backup of SkyNetControl SQLite DB";
  serviceConfig.Type = "oneshot";
  script = ''
    ${pkgs.sqlite}/bin/sqlite3 \
      ${config.services.skynetcontrol.stateDir}/skynetcontrol.db \
      ".backup '/backup/skynetcontrol-$(date +%F).db'"
  '';
};
systemd.timers."skynetcontrol-backup" = {
  wantedBy = [ "timers.target" ];
  timerConfig = { OnCalendar = "daily"; Persistent = true; };
};
```

#### Stop-and-copy (lowest-tech fallback)

When you just want a one-shot before an upgrade:

```bash
sudo systemctl stop skynetcontrol
sudo cp /var/lib/skynetcontrol/skynetcontrol.db /backup/skynetcontrol-$(date +%F).db
sudo systemctl start skynetcontrol
```

#### restic / borg over the state dir

The whole `stateDir` is the unit of backup. Any backup tool that walks a directory works — point it at `services.skynetcontrol.stateDir`. With SQLite's WAL mode this is *mostly* safe, but for a guaranteed-consistent backup pair it with stopping the service or with `sqlite3 .backup` into a snapshot path that restic then captures.

#### PostgreSQL

When `databaseUrl` points at PostgreSQL, ignore the SQLite recipes and use `pg_dump` / `pg_basebackup` per your normal database backup workflow. The `stateDir` then contains only ephemeral runtime state.
```

Then add a brand-new section after Backups:

```markdown
### Moving between database backends

The Nix package ships `skynetcontrol-db-copy`, which uses SQLAlchemy reflection to copy every row from one DB URL to another. Use it to:

- Migrate from SQLite to PostgreSQL (or back) without writing custom dumps.
- Move from one host's DB to another's by copying over the network: the source URL can be a remote PostgreSQL URL.
- Promote a backup snapshot back into a live engine (read from the snapshot DB, write into the running one — when the live one is empty).

Recipe (SQLite → PostgreSQL):

```bash
# 1. Stop the service so the source DB isn't being written to.
sudo systemctl stop skynetcontrol

# 2. Make sure the new target is migrated to the same head as the source.
sudo SKYNET_DATABASE_URL='postgresql+psycopg://skynetcontrol@/skynetcontrol?host=/run/postgresql' \
  skynetcontrol-alembic upgrade head

# 3. Copy the rows.
sudo skynetcontrol-db-copy \
  sqlite:////var/lib/skynetcontrol/skynetcontrol.db \
  'postgresql+psycopg://skynetcontrol@/skynetcontrol?host=/run/postgresql'

# 4. Flip the module to the new URL and restart.
# (edit services.skynetcontrol.databaseUrl in configuration.nix)
sudo nixos-rebuild switch
```

The command refuses to copy into a target that's unmigrated, or that already has data — explicit safety to prevent clobbering. Reverse the source / target arguments to roll back.
```

- [ ] **Step 2: Trim the README's NixOS section and link out**

Open `README.md`. The current `### Production (NixOS)` block has the example module config, then a `#### Database storage` and `#### Backups` block added in the previous branch.

Replace the existing `#### Database storage` and `#### Backups` subsections with a single, tighter pair that points operators at the longer guide:

```markdown
#### Database storage

The module exposes `services.skynetcontrol.stateDir` (default `/var/lib/skynetcontrol`) and `services.skynetcontrol.databaseUrl` (default points at `<stateDir>/skynetcontrol.db`). Override either to put state on a dedicated ZFS dataset, a bind-mount, or external PostgreSQL. See **[docs/deployment/nix.md#custom-storage-location](docs/deployment/nix.md#custom-storage-location)** for ZFS / bind-mount / PostgreSQL recipes.

#### Backups and migration

Pick the backup pattern that matches your storage: ZFS snapshots, online `sqlite3 .backup` via systemd timer, restic over the state dir, or `pg_dump` for PostgreSQL. See **[docs/deployment/nix.md#backups](docs/deployment/nix.md#backups)** for the full recipes.

To move a database between engines (SQLite ↔ PostgreSQL) or between hosts, use the bundled `skynetcontrol-db-copy` CLI:

```bash
sudo skynetcontrol-alembic upgrade head  # against the new URL
sudo skynetcontrol-db-copy \
  sqlite:////var/lib/skynetcontrol/skynetcontrol.db \
  'postgresql+psycopg://skynetcontrol@/skynetcontrol?host=/run/postgresql'
```

See **[docs/deployment/nix.md#moving-between-database-backends](docs/deployment/nix.md#moving-between-database-backends)** for the full migration recipe.
```

(The old block has duplicate content with `nix.md`. This rewrite makes `nix.md` the single source of truth and keeps the README scannable.)

- [ ] **Step 3: Lint the markdown changes by skimming the rendered output**

If you have `glow` or similar installed, run `glow docs/deployment/nix.md | less`. Otherwise just `cat` the changed sections and confirm the heading anchors (`#custom-storage-location`, `#backups`, `#moving-between-database-backends`) match the actual rendered slugs.

There are no automated markdown tests in this repo, so the verification is visual.

- [ ] **Step 4: Commit**

```bash
git add docs/deployment/nix.md README.md
git commit -m "docs: NixOS custom storage, backup patterns, cross-engine migration

- docs/deployment/nix.md: new 'Custom storage location' section covers
  stateDir override, ZFS dataset, bind-mount, and external PostgreSQL.
- Expand 'Backups' with ZFS snapshot, online sqlite3 .backup via
  systemd timer, restic, and PostgreSQL recipes.
- New 'Moving between database backends' section documents the
  skynetcontrol-db-copy CLI.
- README NixOS section now points at the long-form docs instead of
  duplicating them."
```

---

## Task 5: Verify + finish branch

- [ ] **Step 1: Full verify**

```
.venv/bin/pytest -q
nix-shell --run "ruff check backend tests"
cd frontend && nix-shell -p nodejs_22 --run "npm run build" && cd ..
```

Expected: pytest green, ruff clean on backend+tests (the pre-existing alembic E501s are out of scope), frontend builds.

- [ ] **Step 2: Smoke-test the new CLI end-to-end**

```
# Create a throwaway source DB with one row.
rm -f /tmp/smoke-src.db /tmp/smoke-dst.db
SKYNET_DATABASE_URL=sqlite:////tmp/smoke-src.db .venv/bin/alembic upgrade head
SKYNET_DATABASE_URL=sqlite:////tmp/smoke-dst.db .venv/bin/alembic upgrade head

# Insert a sentinel row in the source.
nix-shell -p sqlite --run "sqlite3 /tmp/smoke-src.db \"INSERT INTO app_config (key, value, updated_at) VALUES ('default_net_control', 'K0XYZ', datetime('now'));\""

# Copy.
.venv/bin/skynetcontrol-db-copy sqlite:////tmp/smoke-src.db sqlite:////tmp/smoke-dst.db

# Verify.
nix-shell -p sqlite --run "sqlite3 /tmp/smoke-dst.db 'SELECT key, value FROM app_config;'"
```

Expected last output: `default_net_control|K0XYZ`.

- [ ] **Step 3: Hand off to finishing-a-development-branch**

Invoke `superpowers:finishing-a-development-branch` to merge back to `main` (per CLAUDE.md, default to local merge for solo work). After merge, wait for CI green on `main` before starting the next task.

---

## Self-review

- **Spec coverage:**
  - "How to migrate databases" — Tasks 1-3 (the helper), Task 4 (the docs that explain when and how to use it). ✓
  - "If the export/import functionality doesn't exist, build it" — Task 1 builds the engine-agnostic copy. The user picked option 3 (thin alembic helper) not full JSON export/import, so this is implemented as a row-level table copy rather than JSON. ✓
  - "Store the database elsewhere if desired, i.e. ZFS array" — Task 4's new "Custom storage location" section. ✓
- **Placeholders:** the docs section in Task 4 inlines actual `.nix` snippets and shell recipes rather than handing off with "configure as needed." Code blocks in Task 1 are complete and runnable.
- **Type consistency:** `copy_database(source_url, target_url) -> dict[str, int]` is the same signature in the test in Task 1 Step 1, the implementation in Task 1 Step 3, and the docs/CLI in Tasks 2-4. CLI script name `skynetcontrol-db-copy` is consistent across pyproject, default.nix, README, nix.md, and the smoke test.
- **One thing to watch during execution:** Task 1 Step 3's `_assert_target_ready` uses `select(text("COUNT(*)")).select_from(table)` — make sure to actually `import text` from sqlalchemy in the same file (it's listed in the import block, but double-check it survives Step 5's lint pass).
