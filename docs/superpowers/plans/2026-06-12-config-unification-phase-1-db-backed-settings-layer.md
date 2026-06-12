# Config Unification — Phase 1: DB-backed Settings Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed accessors over the AppConfig table for OAuth providers, SMTP, and the `setup_completed` sentinel. Pure infrastructure — no callers change, no behaviour change.

**Architecture:** Three new modules under `backend/config_mgmt/`, each owning one structured concept. Each accessor returns an immutable dataclass; upserts/deletes operate on the underlying `app_config` rows using the key prefix as a namespace (`oauth.<slug>.*`, `smtp.*`, `setup_completed`). No new tables, no Alembic migrations, no env scanning. Reads are lazy (no caching) so subsequent phases can edit values and see changes immediately.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x (ORM `Mapped`/`mapped_column` style already in use), pytest with the existing in-memory SQLite fixture pattern (`tests/test_config_mgmt.py:9-16`).

**Spec:** `docs/superpowers/specs/2026-06-12-config-unification-design.md`

---

## File structure

**New files:**

| Path | Responsibility |
|------|----------------|
| `backend/config_mgmt/oauth.py` | `OAuthProviderConfig` dataclass + `get_oauth_provider` / `list_oauth_providers` / `upsert_oauth_provider` / `delete_oauth_provider` |
| `backend/config_mgmt/smtp.py` | `SmtpConfig` dataclass + `get_smtp_config` / `upsert_smtp_config` / `clear_smtp_config` |
| `backend/config_mgmt/setup_state.py` | `is_setup_completed` / `mark_setup_completed` |
| `tests/test_config_mgmt_oauth.py` | OAuth accessor tests |
| `tests/test_config_mgmt_smtp.py` | SMTP accessor tests |
| `tests/test_config_mgmt_setup_state.py` | Setup-state accessor tests |

**Untouched in this phase:** `backend/config_mgmt/service.py` (the existing flat `get_config_value` / `set_config_value` keep working — they're the underlying primitive these accessors build on, but Phase 1 wraps the raw queries directly to keep the new modules independent of the env-fallback path in `service.py`).

**Storage scheme** (extends existing `app_config` table without schema changes):

```
oauth.<slug>.name              str  e.g. "PocketID"
oauth.<slug>.enabled           str  "true" | "false"
oauth.<slug>.client_id         str
oauth.<slug>.client_secret     str
oauth.<slug>.issuer_url        str  empty string for non-OIDC fixed providers

smtp.host                      str
smtp.port                      str  decimal integer as string
smtp.username                  str
smtp.password                  str
smtp.from_address              str
smtp.use_tls                   str  "true" | "false"

setup_completed                str  "true" — sentinel; presence is what matters
```

---

## Task 1: SMTP accessor

**Files:**
- Create: `backend/config_mgmt/smtp.py`
- Test: `tests/test_config_mgmt_smtp.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_mgmt_smtp.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.smtp import (
    SmtpConfig,
    clear_smtp_config,
    get_smtp_config,
    upsert_smtp_config,
)
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_get_returns_none_when_nothing_configured(db: Session):
    assert get_smtp_config(db) is None


def test_get_returns_none_when_only_partial_rows_exist(db: Session):
    # Host missing → treat as not configured.
    db.add(AppConfig(key="smtp.port", value="587"))
    db.commit()
    assert get_smtp_config(db) is None


def test_upsert_and_get_roundtrip(db: Session):
    cfg = SmtpConfig(
        host="smtp.example.org",
        port=587,
        username="user",
        password="pass",
        from_address="net@example.org",
        use_tls=True,
    )
    upsert_smtp_config(db, cfg)
    got = get_smtp_config(db)
    assert got == cfg


def test_upsert_overwrites_existing(db: Session):
    upsert_smtp_config(
        db,
        SmtpConfig(host="old.example.org", port=25, username="u", password="p",
                   from_address="a@b", use_tls=False),
    )
    upsert_smtp_config(
        db,
        SmtpConfig(host="new.example.org", port=587, username="u2", password="p2",
                   from_address="c@d", use_tls=True),
    )
    got = get_smtp_config(db)
    assert got is not None
    assert got.host == "new.example.org"
    assert got.port == 587
    assert got.use_tls is True


def test_clear_removes_all_smtp_rows(db: Session):
    upsert_smtp_config(
        db,
        SmtpConfig(host="smtp.example.org", port=587, username="u", password="p",
                   from_address="a@b", use_tls=True),
    )
    clear_smtp_config(db)
    assert get_smtp_config(db) is None
    # And no orphaned rows left:
    leftover = db.query(AppConfig).filter(AppConfig.key.like("smtp.%")).all()
    assert leftover == []


def test_port_parses_as_int(db: Session):
    # Tolerate string-stored ports — value column is Text.
    db.add(AppConfig(key="smtp.host", value="smtp.example.org"))
    db.add(AppConfig(key="smtp.port", value="2525"))
    db.add(AppConfig(key="smtp.username", value=""))
    db.add(AppConfig(key="smtp.password", value=""))
    db.add(AppConfig(key="smtp.from_address", value=""))
    db.add(AppConfig(key="smtp.use_tls", value="false"))
    db.commit()
    got = get_smtp_config(db)
    assert got is not None
    assert got.port == 2525
    assert isinstance(got.port, int)


def test_use_tls_parses_truthy_strings(db: Session):
    db.add(AppConfig(key="smtp.host", value="smtp.example.org"))
    db.add(AppConfig(key="smtp.port", value="587"))
    db.add(AppConfig(key="smtp.username", value=""))
    db.add(AppConfig(key="smtp.password", value=""))
    db.add(AppConfig(key="smtp.from_address", value=""))
    db.add(AppConfig(key="smtp.use_tls", value="true"))
    db.commit()
    got = get_smtp_config(db)
    assert got is not None and got.use_tls is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config_mgmt_smtp.py -q`
Expected: collection error / import failure — `backend.config_mgmt.smtp` does not exist yet.

- [ ] **Step 3: Implement the module**

Create `backend/config_mgmt/smtp.py`:

```python
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.config_mgmt.models import AppConfig


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_address: str
    use_tls: bool


_KEYS = (
    "smtp.host",
    "smtp.port",
    "smtp.username",
    "smtp.password",
    "smtp.from_address",
    "smtp.use_tls",
)


def _row(db: Session, key: str) -> str | None:
    obj = db.get(AppConfig, key)
    return None if obj is None else obj.value


def get_smtp_config(db: Session) -> SmtpConfig | None:
    """Return the configured SMTP settings, or None if `smtp.host` is unset."""
    host = _row(db, "smtp.host")
    if not host:
        return None
    return SmtpConfig(
        host=host,
        port=int(_row(db, "smtp.port") or "0"),
        username=_row(db, "smtp.username") or "",
        password=_row(db, "smtp.password") or "",
        from_address=_row(db, "smtp.from_address") or "",
        use_tls=(_row(db, "smtp.use_tls") or "false").lower() == "true",
    )


def upsert_smtp_config(db: Session, cfg: SmtpConfig) -> None:
    """Write all SMTP fields to app_config, overwriting any existing values."""
    values = {
        "smtp.host": cfg.host,
        "smtp.port": str(cfg.port),
        "smtp.username": cfg.username,
        "smtp.password": cfg.password,
        "smtp.from_address": cfg.from_address,
        "smtp.use_tls": "true" if cfg.use_tls else "false",
    }
    for key, value in values.items():
        obj = db.get(AppConfig, key)
        if obj is None:
            db.add(AppConfig(key=key, value=value))
        else:
            obj.value = value
    db.commit()


def clear_smtp_config(db: Session) -> None:
    """Remove every `smtp.*` row from app_config."""
    for key in _KEYS:
        obj = db.get(AppConfig, key)
        if obj is not None:
            db.delete(obj)
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config_mgmt_smtp.py -q`
Expected: 7 passed.

- [ ] **Step 5: Run the full suite + ruff**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check backend/ tests/"`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/config_mgmt/smtp.py tests/test_config_mgmt_smtp.py
git commit -m "feat(config): typed SMTP accessor over app_config table

Phase 1 of the config-unification spec: SmtpConfig dataclass + get /
upsert / clear helpers. Storage uses smtp.* keys in the existing
app_config table — no schema change, no callers changed yet."
```

---

## Task 2: OAuth provider accessor

**Files:**
- Create: `backend/config_mgmt/oauth.py`
- Test: `tests/test_config_mgmt_oauth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_mgmt_oauth.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.oauth import (
    OAuthProviderConfig,
    delete_oauth_provider,
    get_oauth_provider,
    list_oauth_providers,
    upsert_oauth_provider,
)
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_get_unknown_slug_returns_none(db: Session):
    assert get_oauth_provider(db, "google") is None


def test_list_empty(db: Session):
    assert list_oauth_providers(db) == []


def test_upsert_and_get_roundtrip(db: Session):
    provider = OAuthProviderConfig(
        slug="google",
        name="Google",
        enabled=True,
        client_id="cid",
        client_secret="csec",
        issuer_url="",
    )
    upsert_oauth_provider(db, provider)
    assert get_oauth_provider(db, "google") == provider


def test_upsert_overwrites(db: Session):
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="google", name="Google", enabled=False,
                            client_id="old", client_secret="old", issuer_url=""),
    )
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="google", name="Google", enabled=True,
                            client_id="new", client_secret="new", issuer_url=""),
    )
    got = get_oauth_provider(db, "google")
    assert got is not None
    assert got.enabled is True
    assert got.client_id == "new"


def test_list_returns_all_slugs(db: Session):
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="google", name="Google", enabled=True,
                            client_id="a", client_secret="b", issuer_url=""),
    )
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="pocketid", name="PocketID", enabled=True,
                            client_id="c", client_secret="d",
                            issuer_url="https://id.example.org"),
    )
    slugs = sorted(p.slug for p in list_oauth_providers(db))
    assert slugs == ["google", "pocketid"]


def test_list_returns_sorted_by_slug(db: Session):
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="zeta", name="Z", enabled=True,
                            client_id="a", client_secret="b", issuer_url=""),
    )
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="alpha", name="A", enabled=True,
                            client_id="c", client_secret="d", issuer_url=""),
    )
    assert [p.slug for p in list_oauth_providers(db)] == ["alpha", "zeta"]


def test_partial_rows_are_ignored_for_list(db: Session):
    # A provider with only a `name` row but no `client_id` is incomplete.
    # list_oauth_providers should still surface it (with empty fields) so
    # the operator can finish the configuration; downstream code is
    # responsible for treating empty credentials as disabled.
    db.add(AppConfig(key="oauth.partial.name", value="Partial"))
    db.commit()
    providers = list_oauth_providers(db)
    assert len(providers) == 1
    assert providers[0].slug == "partial"
    assert providers[0].name == "Partial"
    assert providers[0].client_id == ""


def test_delete_removes_all_rows_for_slug(db: Session):
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="google", name="Google", enabled=True,
                            client_id="a", client_secret="b", issuer_url=""),
    )
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="pocketid", name="PocketID", enabled=True,
                            client_id="c", client_secret="d",
                            issuer_url="https://id.example.org"),
    )
    delete_oauth_provider(db, "google")
    assert get_oauth_provider(db, "google") is None
    assert get_oauth_provider(db, "pocketid") is not None
    # And no orphan google rows:
    leftover = (
        db.query(AppConfig)
        .filter(AppConfig.key.like("oauth.google.%"))
        .all()
    )
    assert leftover == []


def test_enabled_parses_truthy(db: Session):
    db.add(AppConfig(key="oauth.x.name", value="X"))
    db.add(AppConfig(key="oauth.x.enabled", value="true"))
    db.add(AppConfig(key="oauth.x.client_id", value="cid"))
    db.add(AppConfig(key="oauth.x.client_secret", value="csec"))
    db.add(AppConfig(key="oauth.x.issuer_url", value=""))
    db.commit()
    got = get_oauth_provider(db, "x")
    assert got is not None and got.enabled is True


def test_enabled_defaults_to_false(db: Session):
    db.add(AppConfig(key="oauth.x.name", value="X"))
    db.add(AppConfig(key="oauth.x.client_id", value="cid"))
    db.commit()
    got = get_oauth_provider(db, "x")
    assert got is not None and got.enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config_mgmt_oauth.py -q`
Expected: collection / import failure — `backend.config_mgmt.oauth` does not exist yet.

- [ ] **Step 3: Implement the module**

Create `backend/config_mgmt/oauth.py`:

```python
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.config_mgmt.models import AppConfig


@dataclass(frozen=True)
class OAuthProviderConfig:
    slug: str
    name: str
    enabled: bool
    client_id: str
    client_secret: str
    issuer_url: str  # empty string for non-OIDC providers


_FIELDS = ("name", "enabled", "client_id", "client_secret", "issuer_url")


def _key(slug: str, field: str) -> str:
    return f"oauth.{slug}.{field}"


def _row(db: Session, key: str) -> str | None:
    obj = db.get(AppConfig, key)
    return None if obj is None else obj.value


def _build(slug: str, rows: dict[str, str]) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        slug=slug,
        name=rows.get("name", ""),
        enabled=rows.get("enabled", "false").lower() == "true",
        client_id=rows.get("client_id", ""),
        client_secret=rows.get("client_secret", ""),
        issuer_url=rows.get("issuer_url", ""),
    )


def get_oauth_provider(db: Session, slug: str) -> OAuthProviderConfig | None:
    """Return the provider configured under `oauth.<slug>.*`, or None if no rows exist."""
    rows: dict[str, str] = {}
    for field in _FIELDS:
        value = _row(db, _key(slug, field))
        if value is not None:
            rows[field] = value
    if not rows:
        return None
    return _build(slug, rows)


def list_oauth_providers(db: Session) -> list[OAuthProviderConfig]:
    """Return every configured provider, sorted by slug."""
    all_rows = (
        db.query(AppConfig)
        .filter(AppConfig.key.like("oauth.%"))
        .all()
    )
    by_slug: dict[str, dict[str, str]] = {}
    for row in all_rows:
        # key shape: "oauth.<slug>.<field>"
        parts = row.key.split(".", 2)
        if len(parts) != 3:
            continue
        _, slug, field = parts
        if field not in _FIELDS:
            continue
        by_slug.setdefault(slug, {})[field] = row.value
    return [_build(slug, rows) for slug, rows in sorted(by_slug.items())]


def upsert_oauth_provider(db: Session, provider: OAuthProviderConfig) -> None:
    """Write every field of `provider` to app_config, overwriting existing rows."""
    values = {
        "name": provider.name,
        "enabled": "true" if provider.enabled else "false",
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
        "issuer_url": provider.issuer_url,
    }
    for field, value in values.items():
        key = _key(provider.slug, field)
        obj = db.get(AppConfig, key)
        if obj is None:
            db.add(AppConfig(key=key, value=value))
        else:
            obj.value = value
    db.commit()


def delete_oauth_provider(db: Session, slug: str) -> None:
    """Remove every `oauth.<slug>.*` row from app_config."""
    rows = (
        db.query(AppConfig)
        .filter(AppConfig.key.like(f"oauth.{slug}.%"))
        .all()
    )
    for row in rows:
        db.delete(row)
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config_mgmt_oauth.py -q`
Expected: 10 passed.

- [ ] **Step 5: Run the full suite + ruff**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check backend/ tests/"`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/config_mgmt/oauth.py tests/test_config_mgmt_oauth.py
git commit -m "feat(config): typed OAuth provider accessor over app_config

Phase 1 of the config-unification spec: OAuthProviderConfig dataclass +
get / list / upsert / delete helpers. Storage uses oauth.<slug>.* keys
in the existing app_config table — no schema change. Partial rows
surface in list_oauth_providers so the operator can finish configuring
a provider mid-edit."
```

---

## Task 3: Setup-state sentinel

**Files:**
- Create: `backend/config_mgmt/setup_state.py`
- Test: `tests/test_config_mgmt_setup_state.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_mgmt_setup_state.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.setup_state import is_setup_completed, mark_setup_completed
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_unset_is_not_completed(db: Session):
    assert is_setup_completed(db) is False


def test_mark_then_check(db: Session):
    mark_setup_completed(db)
    assert is_setup_completed(db) is True


def test_mark_is_idempotent(db: Session):
    mark_setup_completed(db)
    mark_setup_completed(db)
    assert is_setup_completed(db) is True
    rows = db.query(AppConfig).filter(AppConfig.key == "setup_completed").all()
    assert len(rows) == 1


def test_only_truthy_string_counts_as_completed(db: Session):
    # Pre-Phase-2 deployments may have populated the row by hand; treat
    # presence with a non-truthy value as "not completed" so we don't
    # accidentally short-circuit a wizard that hasn't really finished.
    db.add(AppConfig(key="setup_completed", value="false"))
    db.commit()
    assert is_setup_completed(db) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config_mgmt_setup_state.py -q`
Expected: collection / import failure — `backend.config_mgmt.setup_state` does not exist yet.

- [ ] **Step 3: Implement the module**

Create `backend/config_mgmt/setup_state.py`:

```python
from sqlalchemy.orm import Session

from backend.config_mgmt.models import AppConfig

_SENTINEL_KEY = "setup_completed"


def is_setup_completed(db: Session) -> bool:
    """Return True iff the setup_completed sentinel row exists with value "true"."""
    row = db.get(AppConfig, _SENTINEL_KEY)
    return row is not None and row.value.lower() == "true"


def mark_setup_completed(db: Session) -> None:
    """Set the setup_completed sentinel. Idempotent."""
    row = db.get(AppConfig, _SENTINEL_KEY)
    if row is None:
        db.add(AppConfig(key=_SENTINEL_KEY, value="true"))
    else:
        row.value = "true"
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config_mgmt_setup_state.py -q`
Expected: 4 passed.

- [ ] **Step 5: Run the full suite + ruff**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check backend/ tests/"`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/config_mgmt/setup_state.py tests/test_config_mgmt_setup_state.py
git commit -m "feat(config): setup_completed sentinel helpers

Phase 1 of the config-unification spec: is_setup_completed /
mark_setup_completed. Truthy-only check protects against an
accidentally pre-populated 'false' row from short-circuiting the
wizard in Phase 3."
```

---

## Task 4: Phase 1 documentation note

**Files:**
- Modify: `docs/superpowers/specs/2026-06-12-config-unification-design.md`

- [ ] **Step 1: Update the Phasing section to mark Phase 1 done**

Open the spec and find the line beginning `**Phase 1 — DB-backed Settings layer.**`. Append a sentence linking to this plan so future readers can find it:

Use `Edit` with old_string:

```
**Phase 1 — DB-backed Settings layer.** Add `get_oauth_provider(db, slug)`,
`get_smtp_settings(db)`, etc. as new accessors over the existing AppConfig
table. No callers change. Introduce the `setup_completed` sentinel concept
(no UI wired).
```

and new_string:

```
**Phase 1 — DB-backed Settings layer.** Add `get_oauth_provider(db, slug)`,
`get_smtp_settings(db)`, etc. as new accessors over the existing AppConfig
table. No callers change. Introduce the `setup_completed` sentinel concept
(no UI wired). *Implemented per
`docs/superpowers/plans/2026-06-12-config-unification-phase-1-db-backed-settings-layer.md`.*
```

- [ ] **Step 2: Run the full suite + ruff one last time**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check backend/ tests/"`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-12-config-unification-design.md
git commit -m "docs: link Phase 1 plan from config-unification spec"
```

---

## Out of scope (handled in later phases)

- Switching `init_providers` (`backend/auth/service.py:47`) or
  `backend/app.py:46` to read from the new accessors — Phase 2.
- The Alembic data migration that imports env vars into AppConfig — Phase 2.
- `/setup` route, redirect middleware, or any wizard SPA — Phase 3.
- Recovery CLI, `admin_recovery_tokens` table, `/recovery` route — Phase 4.
- Removing fields from `Settings` or the NixOS module's `settings` attrset — Phase 5.

Anything that *uses* the accessors lives in a future phase; Phase 1 lands the
toolkit and stops.
