# Config Unification — Phase 2a: DB-backed Read Paths + Env Import Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the running app from reading OAuth providers and SMTP settings out of Pydantic `Settings` to reading them lazily out of the AppConfig table. Ship a one-shot Alembic data migration that imports current env values into AppConfig and sets `setup_completed=true`, so existing deployments transparently migrate. Existing env vars become no-ops in Phase 5 — this phase keeps env-var support alive via the migration only.

**Architecture:**

- **OAuth providers** are read lazily on each login. A new `resolve_provider(db, slug)` accessor pulls from `list_oauth_providers(db)` (Phase 1) and merges with the static provider registry (`FIXED_PROVIDERS`). OIDC discovery results are cached in a module-level dict keyed by `discovery_url` (effectively per-issuer) — no TTL for now; Phase 5 can add invalidation when settings change.
- **SMTP** is read lazily on each `send_email` call. `get_smtp_config(db)` returns the current config; `send_email` reads it, opens an SMTP session, sends, closes. No persistent connection, no caching.
- **Environment migration** is a one-shot Alembic data migration. It scans `os.environ` for the legacy patterns (`SKYNET_AUTH_*`, `SKYNET_SMTP__*`, `SKYNET_AUTH_OIDC_*`), writes the matching `oauth.*` / `smtp.*` rows into `app_config`, and sets `setup_completed=true` if any rows already exist or were just written. Idempotent — re-running is a no-op.
- **Phase 1 deferred fixes** (`client_secret` hidden from `__repr__`, slug validation on upsert, strict SMTP port handling) land first as the foundation for the rest.

**Tech Stack:** Same as Phase 1 — Python 3.12, SQLAlchemy 2.x, Alembic for the migration, pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-config-unification-design.md`

---

## File structure

**New files:**

| Path | Responsibility |
|------|----------------|
| `backend/config_mgmt/env_import.py` | Pure function `import_env_to_app_config(db, env)` that scans an env dict and writes matching rows. Called by the Alembic migration and unit-tested directly. |
| `alembic/versions/<sha>_import_env_to_app_config.py` | One-shot Alembic data migration that calls `import_env_to_app_config`. |
| `tests/test_env_import.py` | Unit tests for the env-import helper. |

**Modified files:**

| Path | Change |
|------|--------|
| `backend/config_mgmt/oauth.py` | `client_secret` field gets `repr=False`; `upsert_oauth_provider` validates slug via `validate_slug`. |
| `backend/config_mgmt/smtp.py` | `get_smtp_config` returns `None` when port is missing or unparseable, not just when host is missing. |
| `backend/auth/providers.py` | `build_providers` and `get_enabled_providers` take `db: Session` instead of `settings: Settings`; read via `list_oauth_providers`. |
| `backend/auth/service.py` | `init_providers(settings)` → `resolve_provider(db, slug)` (lazy). Adds the OIDC discovery cache module-global. |
| `backend/auth/routes.py` | Replace `request.app.state.providers[name]` lookups with `resolve_provider(db, name)`. |
| `backend/auth/email.py` | `send_email` and `notify_*` helpers take `db: Session` instead of `settings: Settings`; read SMTP via `get_smtp_config`. `app_base_url` is read from the module-level `settings` import (stays in env). |
| `backend/app.py` | Remove `app.state.providers = await init_providers(settings)` from lifespan. |
| Multiple test files | Switch from `Settings(auth_*=…, smtp=…)` to populating the DB. |

**Touched test files** (sizing): 7 test files reference the OAuth/SMTP fields of `Settings`. Most either need updating to populate the DB, or — for tests that specifically validate env-var → Settings parsing (`test_config_env_nesting.py`, `test_config_oidc.py`) — need deletion, since that path is being removed in Phase 5 and the test surface is meaningful only while env-based config exists.

For Phase 2a we **keep** `test_config_env_nesting.py` and `test_config_oidc.py` running as long as `Settings` still carries those fields. They get removed in Phase 5.

---

## Task 1: Tighten Phase 1 (deferred review items)

Three small fixes to harden the Phase 1 accessors before Phase 2's callers wire in.

**Files:**
- Modify: `backend/config_mgmt/oauth.py`
- Modify: `backend/config_mgmt/smtp.py`
- Modify: `tests/test_config_mgmt_oauth.py`
- Modify: `tests/test_config_mgmt_smtp.py`

### Step 1.1: Hide `client_secret` from `OAuthProviderConfig.__repr__`

- [ ] **Step 1: Add a failing test**

Append to `tests/test_config_mgmt_oauth.py`:

```python
def test_repr_does_not_leak_client_secret():
    p = OAuthProviderConfig(
        slug="x", name="X", enabled=True,
        client_id="public-id", client_secret="DO-NOT-LEAK",
        issuer_url="",
    )
    assert "DO-NOT-LEAK" not in repr(p)
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/pytest tests/test_config_mgmt_oauth.py::test_repr_does_not_leak_client_secret -q`
Expected: FAIL — `"DO-NOT-LEAK"` is in the default dataclass repr.

- [ ] **Step 3: Add `repr=False` on `client_secret`**

In `backend/config_mgmt/oauth.py`, change the dataclass field:

Replace:
```python
from dataclasses import dataclass
```
with:
```python
from dataclasses import dataclass, field
```

Replace:
```python
@dataclass(frozen=True)
class OAuthProviderConfig:
    slug: str
    name: str
    enabled: bool
    client_id: str
    client_secret: str
    issuer_url: str  # empty string for non-OIDC providers
```
with:
```python
@dataclass(frozen=True)
class OAuthProviderConfig:
    slug: str
    name: str
    enabled: bool
    client_id: str
    client_secret: str = field(repr=False)
    issuer_url: str  # empty string for non-OIDC providers
```

- [ ] **Step 4: Confirm test passes**

Run: `.venv/bin/pytest tests/test_config_mgmt_oauth.py -q`
Expected: 11 passed.

### Step 1.2: Validate slug on upsert

- [ ] **Step 5: Add a failing test**

Append to `tests/test_config_mgmt_oauth.py`:

```python
def test_upsert_rejects_invalid_slug(db: Session):
    bad = OAuthProviderConfig(
        slug="Bad Slug!",  # spaces + uppercase + punctuation: all illegal
        name="X", enabled=True, client_id="a", client_secret="b", issuer_url="",
    )
    with pytest.raises(ValueError, match="slug"):
        upsert_oauth_provider(db, bad)
    # And nothing was written:
    leftover = db.query(AppConfig).filter(AppConfig.key.like("oauth.%")).all()
    assert leftover == []
```

- [ ] **Step 6: Confirm failure**

Run: `.venv/bin/pytest tests/test_config_mgmt_oauth.py::test_upsert_rejects_invalid_slug -q`
Expected: FAIL — `ValueError` not raised.

- [ ] **Step 7: Add validation to `upsert_oauth_provider`**

In `backend/config_mgmt/oauth.py`, add the import at the top:

```python
from backend.auth.oidc_slug import validate_slug
```

And update the function:

```python
def upsert_oauth_provider(db: Session, provider: OAuthProviderConfig) -> None:
    """Write every field of `provider` to app_config, overwriting existing rows.

    Raises ValueError if the slug fails `validate_slug` — slugs become parts
    of LIKE patterns in `delete_oauth_provider` / `list_oauth_providers`, so
    they must match the existing OIDC-slug whitelist.
    """
    err = validate_slug(provider.slug)
    if err is not None:
        raise ValueError(f"invalid OAuth provider slug {provider.slug!r}: {err}")
    values = {
        "name": provider.name,
        "enabled": "true" if provider.enabled else "false",
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
        "issuer_url": provider.issuer_url,
    }
    for field_name, value in values.items():
        key = _key(provider.slug, field_name)
        obj = db.get(AppConfig, key)
        if obj is None:
            db.add(AppConfig(key=key, value=value))
        else:
            obj.value = value
    db.commit()
```

(Note: `field` local-variable rename to `field_name` avoids shadowing the freshly imported `dataclasses.field`.)

- [ ] **Step 8: Confirm test passes + check the `validate_slug` rejects reserved names**

`validate_slug` rejects `google`, `microsoft`, `github`, `discord`, `facebook`, and the literal `oidc` (see `backend/auth/oidc_slug.py:RESERVED_SLUGS`). But Phase 2's wizard and the migration both write the fixed providers under exactly those slugs. So `validate_slug` is too strict for the storage layer.

Update `backend/config_mgmt/oauth.py` to use a slug check that allows the fixed-provider slugs:

```python
from backend.auth.oidc_slug import RESERVED_SLUGS, validate_slug


_FIXED_SLUGS = RESERVED_SLUGS - {"oidc"}  # google, microsoft, github, discord, facebook


def _check_slug(slug: str) -> None:
    if slug in _FIXED_SLUGS:
        return  # allowed for storage, reserved against user-chosen OIDC slugs
    err = validate_slug(slug)
    if err is not None:
        raise ValueError(f"invalid OAuth provider slug {slug!r}: {err}")
```

And in `upsert_oauth_provider`, replace the `err = validate_slug(...)` lines with `_check_slug(provider.slug)`.

- [ ] **Step 9: Add another test confirming fixed-provider slugs are accepted**

Append:

```python
@pytest.mark.parametrize("slug", ["google", "microsoft", "github", "discord", "facebook"])
def test_upsert_accepts_fixed_provider_slugs(db: Session, slug: str):
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug=slug, name=slug.title(), enabled=True,
                            client_id="a", client_secret="b", issuer_url=""),
    )
    got = get_oauth_provider(db, slug)
    assert got is not None and got.slug == slug


def test_upsert_rejects_literal_oidc_slug(db: Session):
    with pytest.raises(ValueError, match="reserved"):
        upsert_oauth_provider(
            db,
            OAuthProviderConfig(slug="oidc", name="X", enabled=True,
                                client_id="a", client_secret="b", issuer_url=""),
        )
```

- [ ] **Step 10: Run all OAuth tests**

Run: `.venv/bin/pytest tests/test_config_mgmt_oauth.py -q`
Expected: 18 passed (10 original + 1 repr + 1 invalid slug + 5 parametrised fixed + 1 reserved oidc).

### Step 1.3: SMTP port strict handling

- [ ] **Step 11: Add failing tests**

Append to `tests/test_config_mgmt_smtp.py`:

```python
def test_get_returns_none_when_port_missing(db: Session):
    db.add(AppConfig(key="smtp.host", value="smtp.example.org"))
    # No smtp.port row.
    db.commit()
    assert get_smtp_config(db) is None


def test_get_returns_none_when_port_unparseable(db: Session):
    db.add(AppConfig(key="smtp.host", value="smtp.example.org"))
    db.add(AppConfig(key="smtp.port", value="not-a-number"))
    db.add(AppConfig(key="smtp.username", value=""))
    db.add(AppConfig(key="smtp.password", value=""))
    db.add(AppConfig(key="smtp.from_address", value=""))
    db.add(AppConfig(key="smtp.use_tls", value="false"))
    db.commit()
    assert get_smtp_config(db) is None
```

- [ ] **Step 12: Confirm both fail**

Run: `.venv/bin/pytest tests/test_config_mgmt_smtp.py -q`
Expected: 2 failures — first returns `SmtpConfig(port=0, …)`, second raises `ValueError`.

- [ ] **Step 13: Update `get_smtp_config`**

In `backend/config_mgmt/smtp.py`, replace `get_smtp_config` with:

```python
def get_smtp_config(db: Session) -> SmtpConfig | None:
    """Return the configured SMTP settings, or None if host or port are missing or invalid.

    A missing/blank/unparseable `smtp.port` is treated as "not configured"
    rather than silently coerced to 0 — port 0 would later produce an
    obscure SMTP connection failure that's harder to diagnose than a
    no-op email send.
    """
    host = _row(db, "smtp.host")
    if not host:
        return None
    port_raw = _row(db, "smtp.port")
    if not port_raw:
        return None
    try:
        port = int(port_raw)
    except ValueError:
        return None
    return SmtpConfig(
        host=host,
        port=port,
        username=_row(db, "smtp.username") or "",
        password=_row(db, "smtp.password") or "",
        from_address=_row(db, "smtp.from_address") or "",
        use_tls=(_row(db, "smtp.use_tls") or "false").lower() == "true",
    )
```

- [ ] **Step 14: Confirm SMTP tests pass**

Run: `.venv/bin/pytest tests/test_config_mgmt_smtp.py -q`
Expected: 9 passed (7 original + 2 new).

### Step 1.4: Final verification + commit

- [ ] **Step 15: Full suite + ruff**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check backend/ tests/"`
Expected: all green.

- [ ] **Step 16: Commit**

```bash
git add backend/config_mgmt/oauth.py backend/config_mgmt/smtp.py \
        tests/test_config_mgmt_oauth.py tests/test_config_mgmt_smtp.py
git commit -m "fix(config): tighten Phase 1 accessors before Phase 2 wiring

Three review-flagged items addressed before Phase 2 wires callers into
the new accessors:

- OAuthProviderConfig.client_secret is now repr=False so logging the
  dataclass doesn't leak credentials.
- upsert_oauth_provider validates the slug against the existing OIDC
  whitelist (allowing the five fixed-provider slugs as well). Slugs
  flow into LIKE patterns in delete / list, so invalid input now fails
  at write time instead of silently malforming SQL.
- get_smtp_config returns None when smtp.port is missing or
  unparseable, instead of silently coercing to port 0 (which produced
  an obscure SMTP connection failure downstream)."
```

---

## Task 2: Lazy DB-backed OAuth provider read path

Switch all OAuth provider reads from `Settings` to AppConfig. Make `resolve_provider(db, slug)` a lazy per-login operation. Cache OIDC discovery URLs in a module-level dict. Remove `app.state.providers` startup init. Update affected routes and tests.

**Files:**
- Modify: `backend/auth/providers.py`
- Modify: `backend/auth/service.py`
- Modify: `backend/auth/routes.py`
- Modify: `backend/app.py`
- Modify: `tests/test_auth_providers.py`
- Modify: `tests/test_auth_service_discovery.py`
- Modify: `tests/test_auth_routes.py`
- Modify: `tests/test_auth_registration.py`
- Modify: `tests/conftest.py` (add a `seed_oauth_provider` fixture)

### Step 2.1: Read the existing call shape

- [ ] **Step 1: Print the current public surface**

Run these and read the output before touching anything:

```bash
grep -n "def " backend/auth/providers.py
grep -n "def " backend/auth/service.py
grep -n "request.app.state.providers\|app.state.providers" backend/ -r --include='*.py'
```

Confirm callers are:
- `backend/app.py:46` — `app.state.providers = await init_providers(settings)`
- `backend/auth/routes.py:30, 38` — `providers = request.app.state.providers; provider_config = providers.get(name)`

### Step 2.2: Introduce `seed_oauth_provider` test fixture

- [ ] **Step 2: Append a fixture to `tests/conftest.py`**

Add at the end:

```python
import pytest
from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider


@pytest.fixture
def seed_oauth_provider():
    """Return a callable that writes an OAuth provider into the AppConfig table.

    Usage in tests:

        def test_foo(db, seed_oauth_provider):
            seed_oauth_provider("google", client_id="cid", client_secret="csec")
    """
    def _seed(slug: str, **overrides):
        provider = OAuthProviderConfig(
            slug=slug,
            name=overrides.get("name", slug.title()),
            enabled=overrides.get("enabled", True),
            client_id=overrides.get("client_id", "test-cid"),
            client_secret=overrides.get("client_secret", "test-csec"),
            issuer_url=overrides.get("issuer_url", ""),
        )
        # The caller is responsible for providing a `db` session via the
        # closure — we use a relative import so this fixture works whether
        # the test uses an integration session or a unit-test session.
        return provider, _persist
    def _persist(db, provider):
        upsert_oauth_provider(db, provider)
        return provider
    return _seed
```

Actually that's awkward — fixtures combine poorly with stateless helper functions when the DB session is per-test. Simpler approach:

Replace the above with:

```python
@pytest.fixture
def seed_oauth_provider():
    """Factory that writes an OAuth provider row given a db session.

    Usage:
        def test_x(db_session, seed_oauth_provider):
            seed_oauth_provider(db_session, "google", client_id="cid", client_secret="csec")
    """
    from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider

    def _seed(db, slug: str, **overrides):
        upsert_oauth_provider(db, OAuthProviderConfig(
            slug=slug,
            name=overrides.get("name", slug.title()),
            enabled=overrides.get("enabled", True),
            client_id=overrides.get("client_id", "test-cid"),
            client_secret=overrides.get("client_secret", "test-csec"),
            issuer_url=overrides.get("issuer_url", ""),
        ))
    return _seed
```

- [ ] **Step 3: Confirm conftest still imports cleanly**

Run: `.venv/bin/pytest --collect-only -q 2>&1 | tail -5`
Expected: collection succeeds.

### Step 2.3: Rewrite `backend/auth/providers.py` to read from DB

- [ ] **Step 4: Write the failing tests against the new API**

Replace the contents of `tests/test_auth_providers.py` with new tests that exercise the DB-based API. Read the existing file first to keep any test that's still relevant. The new public API is:

```python
def build_providers(db: Session) -> dict[str, ProviderConfig]: ...
def get_enabled_providers(db: Session) -> dict[str, OAuthProviderConfig]: ...
```

Tests:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.auth.providers import (
    FIXED_PROVIDERS,
    build_providers,
    get_enabled_providers,
)
from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def _seed(db, slug, **kw):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug=slug, name=kw.get("name", slug.title()),
        enabled=kw.get("enabled", True),
        client_id=kw.get("client_id", "cid"),
        client_secret=kw.get("client_secret", "csec"),
        issuer_url=kw.get("issuer_url", ""),
    ))


def test_get_enabled_providers_reads_from_db(db):
    _seed(db, "google", client_id="goog-id")
    _seed(db, "github", enabled=False)
    enabled = get_enabled_providers(db)
    assert "google" in enabled
    assert "github" not in enabled  # disabled
    assert enabled["google"].client_id == "goog-id"


def test_get_enabled_providers_includes_custom_oidc(db):
    _seed(db, "pocketid", name="PocketID", issuer_url="https://id.example.org")
    enabled = get_enabled_providers(db)
    assert "pocketid" in enabled
    assert enabled["pocketid"].issuer_url == "https://id.example.org"


def test_build_providers_merges_db_oidc_with_fixed_registry(db):
    _seed(db, "pocketid", name="PocketID", issuer_url="https://id.example.org")
    providers = build_providers(db)
    # Fixed providers still present:
    for fixed_slug in ("google", "microsoft", "github"):
        assert fixed_slug in providers
        assert providers[fixed_slug] is FIXED_PROVIDERS[fixed_slug] or \
               providers[fixed_slug].label == FIXED_PROVIDERS[fixed_slug].label
    # Dynamic provider added:
    assert "pocketid" in providers
    assert providers["pocketid"].label == "PocketID"
    assert providers["pocketid"].discovery_url.endswith("/.well-known/openid-configuration")


def test_build_providers_returns_fixed_registry_when_db_empty(db):
    providers = build_providers(db)
    assert set(providers.keys()) == set(FIXED_PROVIDERS.keys())


def test_disabled_provider_omitted_from_enabled(db):
    _seed(db, "google", enabled=False)
    assert "google" not in get_enabled_providers(db)


def test_provider_with_empty_client_id_is_treated_as_disabled(db):
    _seed(db, "google", enabled=True, client_id="", client_secret="")
    assert "google" not in get_enabled_providers(db)
```

- [ ] **Step 5: Confirm failure**

Run: `.venv/bin/pytest tests/test_auth_providers.py -q`
Expected: failures because `build_providers` / `get_enabled_providers` still take `settings`.

- [ ] **Step 6: Rewrite `backend/auth/providers.py`**

Read the current file end-to-end first. Then replace `build_providers` and `get_enabled_providers`:

```python
from backend.config_mgmt.oauth import OAuthProviderConfig, list_oauth_providers


def _normalise_issuer(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith("/.well-known/openid-configuration"):
        return url
    return f"{url}/.well-known/openid-configuration"


def build_providers(db) -> dict[str, ProviderConfig]:
    """Return all known providers — the fixed registry plus any custom OIDC
    providers configured in the AppConfig table — keyed by slug.

    Disabled providers and fixed providers without DB rows still appear; the
    set of *enabled* providers is exposed by `get_enabled_providers`.
    """
    result = dict(FIXED_PROVIDERS)
    for p in list_oauth_providers(db):
        if p.slug in FIXED_PROVIDERS:
            continue  # the fixed registry already has the right ProviderConfig
        result[p.slug] = ProviderConfig(
            protocol="oidc",
            label=p.name or p.slug.title(),
            scopes="openid email profile",
            discovery_url=_normalise_issuer(p.issuer_url) if p.issuer_url else "",
            extract_subject=_oidc_extract_subject,
            extract_name=_oidc_extract_name,
            extract_email=_oidc_extract_email,
        )
    return result


def get_enabled_providers(db) -> dict[str, OAuthProviderConfig]:
    """Return enabled providers keyed by slug.

    A provider is *enabled* if its DB row has enabled=true AND a non-empty
    client_id. The client_id check matches the previous Pydantic behaviour
    where an empty ProviderSettings was effectively unusable.
    """
    enabled: dict[str, OAuthProviderConfig] = {}
    for p in list_oauth_providers(db):
        if p.enabled and p.client_id:
            enabled[p.slug] = p
    return enabled
```

The top-of-file imports also change — remove `from backend.config import Settings, ProviderSettings, OIDCProviderConfig` and any `Settings`-typed references in the file.

- [ ] **Step 7: Run the new tests**

Run: `.venv/bin/pytest tests/test_auth_providers.py -q`
Expected: 6 passed.

### Step 2.4: Replace `init_providers` with a lazy `resolve_provider`

- [ ] **Step 8: Write tests for the lazy resolver**

Replace `tests/test_auth_service_discovery.py` with tests against the new API. Read the existing file first. New API:

```python
async def resolve_provider(db: Session, slug: str) -> dict | None: ...
```

Returns `None` if the provider isn't enabled, otherwise the resolved dict with `authorize_url`, `token_url`, `userinfo_url`, `client_id`, `client_secret`, `scopes`, `label`, `protocol`, `extract_*`.

For OIDC, fetches discovery from a module-level cache keyed by `discovery_url`.

Tests:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from unittest.mock import patch, AsyncMock

from backend.auth.service import resolve_provider, _DISCOVERY_CACHE
from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider
from backend.db.base import Base


@pytest.fixture(autouse=True)
def clear_discovery_cache():
    _DISCOVERY_CACHE.clear()
    yield
    _DISCOVERY_CACHE.clear()


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.mark.asyncio
async def test_resolve_provider_returns_none_when_not_enabled(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=False,
        client_id="c", client_secret="s", issuer_url="",
    ))
    assert await resolve_provider(db, "google") is None


@pytest.mark.asyncio
async def test_resolve_provider_unknown_slug_returns_none(db):
    assert await resolve_provider(db, "nonexistent") is None


@pytest.mark.asyncio
async def test_resolve_provider_oauth2_no_discovery(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="github", name="GitHub", enabled=True,
        client_id="ghc", client_secret="ghs", issuer_url="",
    ))
    resolved = await resolve_provider(db, "github")
    assert resolved is not None
    assert resolved["client_id"] == "ghc"
    assert resolved["protocol"] == "oauth2"
    assert resolved["authorize_url"].startswith("https://github.com")


@pytest.mark.asyncio
async def test_resolve_provider_oidc_fetches_discovery(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=True,
        client_id="gc", client_secret="gs", issuer_url="",
    ))
    with patch("backend.auth.service.fetch_oidc_discovery",
               new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {
            "authorization_endpoint": "https://x/auth",
            "token_endpoint": "https://x/token",
            "userinfo_endpoint": "https://x/userinfo",
        }
        resolved = await resolve_provider(db, "google")
    assert resolved is not None
    assert resolved["authorize_url"] == "https://x/auth"


@pytest.mark.asyncio
async def test_resolve_provider_caches_oidc_discovery(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=True,
        client_id="gc", client_secret="gs", issuer_url="",
    ))
    with patch("backend.auth.service.fetch_oidc_discovery",
               new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {
            "authorization_endpoint": "https://x/auth",
            "token_endpoint": "https://x/token",
            "userinfo_endpoint": "https://x/userinfo",
        }
        await resolve_provider(db, "google")
        await resolve_provider(db, "google")
    assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_resolve_provider_returns_none_when_discovery_fails(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=True,
        client_id="gc", client_secret="gs", issuer_url="",
    ))
    with patch("backend.auth.service.fetch_oidc_discovery",
               new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = None
        assert await resolve_provider(db, "google") is None
```

- [ ] **Step 9: Confirm failure (resolve_provider doesn't exist yet)**

Run: `.venv/bin/pytest tests/test_auth_service_discovery.py -q`
Expected: import errors / failures.

- [ ] **Step 10: Implement `resolve_provider` in `backend/auth/service.py`**

Read the existing file first. Replace `init_providers` and its supporting code with:

```python
# Module-level cache: discovery_url -> resolved discovery dict.
# Phase 2a accepts no TTL: cache lives for the process lifetime. OIDC
# providers rotate endpoints rarely enough that a restart suffices. If
# this becomes a problem the cache can grow a TTL or an invalidation hook
# tied to upsert_oauth_provider.
_DISCOVERY_CACHE: dict[str, dict] = {}


async def _get_discovery(discovery_url: str) -> dict | None:
    cached = _DISCOVERY_CACHE.get(discovery_url)
    if cached is not None:
        return cached
    discovery = await fetch_oidc_discovery(discovery_url)
    if discovery is not None:
        _DISCOVERY_CACHE[discovery_url] = discovery
    return discovery


async def resolve_provider(db, slug: str) -> dict | None:
    """Lazily resolve a single provider for a login flow.

    Reads the provider's credentials from the AppConfig table (Phase 1),
    merges with the static provider registry (FIXED_PROVIDERS or the
    dynamic OIDC entry from build_providers), and — for OIDC — fetches
    or reads from the discovery cache.

    Returns the resolved auth-flow dict (with authorize/token/userinfo
    URLs filled in), or None if the provider is unknown, disabled,
    has no client_id, or — for OIDC — discovery failed.
    """
    enabled = get_enabled_providers(db)
    provider_settings = enabled.get(slug)
    if provider_settings is None:
        return None

    registry = build_providers(db)
    config = registry.get(slug)
    if config is None:
        return None

    if config.protocol == "oidc":
        discovery = await _get_discovery(config.discovery_url) if config.discovery_url else None
        if discovery is None:
            logger.warning("resolve_provider(%s): OIDC discovery failed", slug)
            return None
        authorize_url = discovery.get("authorization_endpoint", "")
        token_url = discovery.get("token_endpoint", "")
        userinfo_url = discovery.get("userinfo_endpoint", "")
    else:
        authorize_url = config.authorize_url
        token_url = config.token_url
        userinfo_url = config.userinfo_url

    return {
        "authorize_url": authorize_url,
        "token_url": token_url,
        "userinfo_url": userinfo_url,
        "client_id": provider_settings.client_id,
        "client_secret": provider_settings.client_secret,
        "scopes": config.scopes,
        "label": config.label,
        "protocol": config.protocol,
        "extract_subject": config.extract_subject,
        "extract_name": config.extract_name,
        "extract_email": config.extract_email,
    }
```

Remove `init_providers` entirely.

The top imports update — remove `from backend.config import Settings` and the `build_providers, get_enabled_providers` import becomes `from backend.auth.providers import build_providers, get_enabled_providers` (already correct, but `Settings` reference goes away).

- [ ] **Step 11: Run new tests**

Run: `.venv/bin/pytest tests/test_auth_service_discovery.py -q`
Expected: 6 passed.

### Step 2.5: Switch routes to use `resolve_provider`

- [ ] **Step 12: Read existing routes**

Read `backend/auth/routes.py` lines 20–80, find every `request.app.state.providers` reference and the lookup pattern.

- [ ] **Step 13: Replace**

Replace each instance of:

```python
providers = request.app.state.providers
provider_config = providers.get(name)
if provider_config is None:
    ...
```

with:

```python
from backend.auth.service import resolve_provider  # add to top imports

provider_config = await resolve_provider(db, name)
if provider_config is None:
    ...
```

(`db: Session = Depends(get_db_session)` should already be in scope at these route handlers; if not, add it as a dependency.)

- [ ] **Step 14: Run the auth route tests**

Run: `.venv/bin/pytest tests/test_auth_routes.py tests/test_auth_registration.py -q`
Expected: failures pointing at fixture setup (tests still configure providers via `Settings`). Updating tests is Step 15.

### Step 2.6: Update auth-route tests to seed providers via DB

- [ ] **Step 15: Find every place tests configure providers via `Settings`**

Run: `grep -n "auth_google\|auth_github\|auth_microsoft\|auth_oidc_providers" tests/test_auth_routes.py tests/test_auth_registration.py`

For each match, find the corresponding fixture or setup block and switch to writing via `seed_oauth_provider(db, "google", client_id=..., client_secret=...)` (the conftest fixture). The `Settings(...)` call now contains no provider fields. Note: the `db` argument to `seed_oauth_provider` must be a session bound to the same engine the test app uses — the existing `app` fixture builds engine + session_factory; tests should pull a session from there.

If a test uses `mock_init_providers` or similar machinery to stub `app.state.providers`, it can be deleted — the new lazy path has no startup-time provider state to stub. Mock `resolve_provider` directly instead via `patch("backend.auth.routes.resolve_provider", ...)`.

(This step is necessarily a multi-file edit. Touch only the test files. Don't add or remove production-code paths.)

- [ ] **Step 16: Run the auth route tests until green**

Run: `.venv/bin/pytest tests/test_auth_routes.py tests/test_auth_registration.py -q`
Expected: all pass after the test updates.

### Step 2.7: Remove `app.state.providers` startup init

- [ ] **Step 17: Edit `backend/app.py`**

Find the `lifespan` block (around line 44). Remove these lines:

```python
app.state.providers = await init_providers(settings)
```

And remove the `from backend.auth.service import init_providers` import at the top.

Don't remove the rest of `lifespan` — the scanner-loop setup stays untouched.

- [ ] **Step 18: Run full suite + ruff**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check backend/ tests/"`
Expected: all green.

### Step 2.8: Commit

- [ ] **Step 19: Commit**

```bash
git add backend/auth/providers.py backend/auth/service.py backend/auth/routes.py \
        backend/app.py \
        tests/conftest.py tests/test_auth_providers.py \
        tests/test_auth_service_discovery.py tests/test_auth_routes.py \
        tests/test_auth_registration.py
git commit -m "feat(auth): read OAuth providers from app_config (lazy, no startup init)

Provider credentials and the custom-OIDC list now come from the
oauth.<slug>.* rows in app_config (Phase 1 accessor) rather than from
Settings env vars. The auth flow's hot path becomes lazy:

- resolve_provider(db, slug) replaces init_providers(settings). It is
  called per login attempt, reads provider config from the DB, and
  returns None for unknown / disabled / discovery-failed providers.
- OIDC discovery is cached in a module-level dict keyed by
  discovery_url for the lifetime of the process. No TTL yet (rotation
  is rare; a restart suffices).
- app.state.providers is no longer populated at startup.

Env-var-based providers still work — the Phase 2a Alembic migration
imports them into app_config. Phase 5 removes the env vars themselves."
```

---

## Task 3: Lazy DB-backed SMTP

Switch `send_email` and the `notify_*` helpers to read SMTP from AppConfig per call. `app_base_url` stays in `Settings`; the helpers read it via the module-level `settings` singleton.

**Files:**
- Modify: `backend/auth/email.py`
- Modify: `backend/auth/routes.py`
- Modify: `tests/test_auth_email.py`
- Modify: `tests/test_delivery_email.py`

### Step 3.1: Write the failing tests

- [ ] **Step 1: Read existing email tests**

```bash
grep -n "def test_\|send_email\|notify_" tests/test_auth_email.py
```

- [ ] **Step 2: Update tests to use the DB-based API**

The new signatures are:

```python
async def send_email(db: Session, to: str, subject: str, body: str) -> None: ...
async def notify_admins_new_registration(db: Session, admins: list[User], new_user: User) -> None: ...
# Likewise for the other three notify_* helpers.
```

Tests should:

1. Set up SMTP via `upsert_smtp_config(db, SmtpConfig(...))`.
2. Patch `smtplib.SMTP` or `_send_email_sync` to capture what would have been sent.
3. Assert `send_email` becomes a no-op when no SMTP config row exists (DB is empty).
4. Assert TLS / login behaviour with appropriate config rows.

Rewrite the tests so they pass `db` instead of `settings`. The `app_base_url` substitution in notification bodies is checked against `settings.app_base_url` from `backend.config.settings` (the module-level singleton).

- [ ] **Step 3: Confirm tests fail**

Run: `.venv/bin/pytest tests/test_auth_email.py -q`
Expected: failures — old signatures.

### Step 3.2: Rewrite `backend/auth/email.py`

- [ ] **Step 4: Read the existing file**

Read `backend/auth/email.py` end-to-end.

- [ ] **Step 5: Replace with the DB-backed version**

Replace the entire file with:

```python
import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from backend.auth.models import User
from backend.config import settings  # module singleton — only app_base_url is read
from backend.config_mgmt.smtp import SmtpConfig, get_smtp_config

logger = logging.getLogger(__name__)


async def send_email(db: Session, to: str, subject: str, body: str) -> None:
    """Send an email via SMTP. Fire-and-forget — never raises."""
    smtp = get_smtp_config(db)
    if smtp is None:
        logger.debug("SMTP not configured, skipping email to %s", to)
        return
    try:
        await asyncio.to_thread(_send_email_sync, smtp, to, subject, body)
    except Exception:
        logger.exception("Failed to send email to %s", to)


def _send_email_sync(smtp: SmtpConfig, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp.from_address
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP(smtp.host, smtp.port) as server:
        if smtp.use_tls:
            server.starttls(context=ssl.create_default_context())
        if smtp.username:
            server.login(smtp.username, smtp.password)
        server.send_message(msg)


async def notify_admins_new_registration(db: Session, admins: list[User], new_user: User) -> None:
    base_url = settings.app_base_url
    for admin in admins:
        if admin.email:
            await send_email(
                db,
                admin.email,
                f"[SkyNetControl] New registration: {new_user.callsign}",
                f"{new_user.name} has registered as {new_user.callsign} and is awaiting approval. "
                f"Review pending users at {base_url}.",
            )


async def notify_admins_callsign_change(db: Session, admins: list[User], user: User, new_callsign: str) -> None:
    base_url = settings.app_base_url
    for admin in admins:
        if admin.email:
            await send_email(
                db,
                admin.email,
                f"[SkyNetControl] Callsign change request: {user.callsign} -> {new_callsign}",
                f"{user.name} ({user.callsign}) has requested a callsign change to {new_callsign}. "
                f"Review at {base_url}.",
            )


async def notify_user_approved(db: Session, user: User) -> None:
    if not user.email:
        return
    base_url = settings.app_base_url
    await send_email(
        db,
        user.email,
        "[SkyNetControl] Your account has been approved",
        f"Your account ({user.callsign}) has been approved as {user.role.value}. "
        f"You can now access SkyNetControl at {base_url}.",
    )


async def notify_user_callsign_approved(db: Session, user: User, old_callsign: str) -> None:
    if not user.email:
        return
    base_url = settings.app_base_url
    await send_email(
        db,
        user.email,
        "[SkyNetControl] Your callsign change has been approved",
        f"Your callsign has been changed from {old_callsign} to {user.callsign}. "
        f"Access SkyNetControl at {base_url}.",
    )
```

- [ ] **Step 6: Update callers in `backend/auth/routes.py`**

Find every `notify_*` call (4 of them, around lines 206, 242, 299, 342). For each, change `notify_x(admins, user, app_settings)` to `notify_x(db, admins, user)`. Remove now-unused `app_settings` arguments where they were only there for these calls.

- [ ] **Step 7: Run email tests**

Run: `.venv/bin/pytest tests/test_auth_email.py -q`
Expected: pass.

- [ ] **Step 8: Run the broader suite**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check backend/ tests/"`
Expected: all green. The integration tests (`test_auth_routes.py`, `test_auth_registration.py`) shouldn't care about SMTP — they were already mocking it or running with SMTP unconfigured.

### Step 3.3: Commit

- [ ] **Step 9: Commit**

```bash
git add backend/auth/email.py backend/auth/routes.py \
        tests/test_auth_email.py tests/test_delivery_email.py
git commit -m "feat(email): read SMTP from app_config per-send

send_email and the notify_* helpers now take a db Session instead of a
Settings object and read SMTP via get_smtp_config. app_base_url is
still in env and is read from the module-level settings singleton.
SMTP changes via the (future) Config page take effect immediately
without a restart."
```

---

## Task 4: Alembic env-import migration

A one-shot data migration that scans `os.environ` for the legacy env-var patterns and writes matching rows into `app_config`. Idempotent. Extracts the import logic into a pure function for testability.

**Files:**
- Create: `backend/config_mgmt/env_import.py`
- Create: `alembic/versions/<sha>_import_env_to_app_config.py`
- Create: `tests/test_env_import.py`

### Step 4.1: Write tests for the import helper

- [ ] **Step 1: Author the test file**

Create `tests/test_env_import.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config_mgmt.env_import import import_env_to_app_config
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.oauth import get_oauth_provider, list_oauth_providers
from backend.config_mgmt.setup_state import is_setup_completed
from backend.config_mgmt.smtp import get_smtp_config
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_empty_env_with_empty_db_does_not_mark_setup_complete(db: Session):
    import_env_to_app_config(db, {})
    assert is_setup_completed(db) is False


def test_empty_env_with_existing_db_rows_marks_setup_complete(db: Session):
    # Pretend the user already populated net_address via the (future)
    # Config page — we shouldn't relaunch the wizard on next boot.
    db.add(AppConfig(key="net_address", value="w0ne@winlink.org"))
    db.commit()
    import_env_to_app_config(db, {})
    assert is_setup_completed(db) is True


def test_imports_fixed_provider_from_env(db: Session):
    env = {
        "SKYNET_AUTH_GOOGLE__ENABLED": "true",
        "SKYNET_AUTH_GOOGLE__CLIENT_ID": "google-cid",
        "SKYNET_AUTH_GOOGLE__CLIENT_SECRET": "google-csec",
    }
    import_env_to_app_config(db, env)
    google = get_oauth_provider(db, "google")
    assert google is not None
    assert google.enabled is True
    assert google.client_id == "google-cid"
    assert google.client_secret == "google-csec"
    assert google.name == "Google"
    assert google.issuer_url == ""
    assert is_setup_completed(db) is True


def test_imports_oidc_provider_from_env(db: Session):
    env = {
        "SKYNET_AUTH_OIDC_POCKETID_NAME": "PocketID",
        "SKYNET_AUTH_OIDC_POCKETID_ENABLED": "true",
        "SKYNET_AUTH_OIDC_POCKETID_CLIENT_ID": "pocket-cid",
        "SKYNET_AUTH_OIDC_POCKETID_CLIENT_SECRET": "pocket-csec",
        "SKYNET_AUTH_OIDC_POCKETID_ISSUER_URL": "https://id.example.org",
    }
    import_env_to_app_config(db, env)
    pocket = get_oauth_provider(db, "pocketid")
    assert pocket is not None
    assert pocket.name == "PocketID"
    assert pocket.issuer_url == "https://id.example.org"


def test_imports_smtp_from_env(db: Session):
    env = {
        "SKYNET_SMTP__HOST": "smtp.example.org",
        "SKYNET_SMTP__PORT": "587",
        "SKYNET_SMTP__USERNAME": "user",
        "SKYNET_SMTP__PASSWORD": "pass",
        "SKYNET_SMTP__FROM_ADDRESS": "net@example.org",
        "SKYNET_SMTP__USE_TLS": "true",
    }
    import_env_to_app_config(db, env)
    smtp = get_smtp_config(db)
    assert smtp is not None
    assert smtp.host == "smtp.example.org"
    assert smtp.port == 587
    assert smtp.use_tls is True


def test_idempotent_on_second_run(db: Session):
    env = {
        "SKYNET_AUTH_GOOGLE__ENABLED": "true",
        "SKYNET_AUTH_GOOGLE__CLIENT_ID": "google-cid",
        "SKYNET_AUTH_GOOGLE__CLIENT_SECRET": "google-csec",
    }
    import_env_to_app_config(db, env)
    # Mutate the row manually to simulate an admin edit, then re-run
    # the import; the admin's value must NOT be overwritten.
    from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=True,
        client_id="admin-edited-cid", client_secret="admin-edited-csec",
        issuer_url="",
    ))
    import_env_to_app_config(db, env)
    google = get_oauth_provider(db, "google")
    assert google is not None
    assert google.client_id == "admin-edited-cid"  # not the env value


def test_skips_disabled_fixed_provider_with_no_credentials(db: Session):
    env = {
        "SKYNET_AUTH_GOOGLE__ENABLED": "false",
        # No client_id / client_secret
    }
    import_env_to_app_config(db, env)
    # No oauth.* rows written; nothing else to do.
    rows = list_oauth_providers(db)
    assert rows == []


def test_invalid_oidc_slug_is_skipped(db: Session):
    # If someone set SKYNET_AUTH_OIDC_GOOGLE_* (slug "google" is reserved),
    # the migration should skip it rather than crash.
    env = {
        "SKYNET_AUTH_OIDC_GOOGLE_NAME": "Custom Google",
        "SKYNET_AUTH_OIDC_GOOGLE_ENABLED": "true",
        "SKYNET_AUTH_OIDC_GOOGLE_CLIENT_ID": "x",
        "SKYNET_AUTH_OIDC_GOOGLE_CLIENT_SECRET": "y",
        "SKYNET_AUTH_OIDC_GOOGLE_ISSUER_URL": "https://example.org",
    }
    import_env_to_app_config(db, env)
    # Reserved slug skipped:
    assert get_oauth_provider(db, "google") is None
```

- [ ] **Step 2: Confirm failure**

Run: `.venv/bin/pytest tests/test_env_import.py -q`
Expected: import error — `backend.config_mgmt.env_import` doesn't exist.

### Step 4.2: Implement the import helper

- [ ] **Step 3: Create `backend/config_mgmt/env_import.py`**

```python
"""One-shot env-to-AppConfig importer.

Scans an env-var mapping for the legacy SkyNetControl patterns
(`SKYNET_AUTH_*`, `SKYNET_SMTP__*`, `SKYNET_AUTH_OIDC_*_*`) and writes
the matching `oauth.<slug>.*` / `smtp.*` rows into `app_config`. Called
by the Alembic data migration that lands alongside Phase 2a, and unit
tested directly.

Idempotent: existing `oauth.*` / `smtp.*` rows are never overwritten —
if any are already present for a given slug or for SMTP, the env values
for that section are ignored. This protects against the migration ever
clobbering admin edits made via the Config page.

If any oauth/smtp rows were written OR any pre-existing AppConfig rows
were present before the call, `setup_completed` is marked true so the
existing deployment skips the wizard on next boot.
"""

from __future__ import annotations

import re
from typing import Mapping

from sqlalchemy.orm import Session

from backend.auth.oidc_slug import slug_from_env_middle, validate_slug
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.oauth import (
    OAuthProviderConfig,
    get_oauth_provider,
    upsert_oauth_provider,
)
from backend.config_mgmt.setup_state import mark_setup_completed
from backend.config_mgmt.smtp import SmtpConfig, get_smtp_config, upsert_smtp_config

_FIXED_PROVIDERS = ("google", "microsoft", "github", "discord", "facebook")

_OIDC_ENV_RE = re.compile(
    r"^SKYNET_AUTH_OIDC_([A-Z0-9_]+)_(NAME|ENABLED|CLIENT_ID|CLIENT_SECRET|ISSUER_URL)$"
)
_FIXED_ENV_RE = re.compile(
    r"^SKYNET_AUTH_(GOOGLE|MICROSOFT|GITHUB|DISCORD|FACEBOOK)__(ENABLED|CLIENT_ID|CLIENT_SECRET)$"
)
_SMTP_KEYS = {
    "SKYNET_SMTP__HOST":          "host",
    "SKYNET_SMTP__PORT":          "port",
    "SKYNET_SMTP__USERNAME":      "username",
    "SKYNET_SMTP__PASSWORD":      "password",
    "SKYNET_SMTP__FROM_ADDRESS":  "from_address",
    "SKYNET_SMTP__USE_TLS":       "use_tls",
}


def import_env_to_app_config(db: Session, env: Mapping[str, str]) -> None:
    """Scan `env` for the legacy SKYNET_* patterns and persist them.

    Existing rows are never overwritten. After the scan, `setup_completed`
    is marked true if anything is present in `app_config` (whether just
    written, or pre-existing from prior wizard runs).
    """
    pre_existing_rows = db.query(AppConfig).count()

    _import_fixed_providers(db, env)
    _import_oidc_providers(db, env)
    _import_smtp(db, env)

    if pre_existing_rows > 0 or db.query(AppConfig).count() > 0:
        mark_setup_completed(db)


def _import_fixed_providers(db: Session, env: Mapping[str, str]) -> None:
    by_slug: dict[str, dict[str, str]] = {}
    for key, value in env.items():
        m = _FIXED_ENV_RE.match(key)
        if not m:
            continue
        slug = m.group(1).lower()
        field = m.group(2).lower()
        by_slug.setdefault(slug, {})[field] = value

    for slug, fields in by_slug.items():
        if get_oauth_provider(db, slug) is not None:
            continue  # never overwrite admin/wizard data
        enabled = fields.get("enabled", "false").lower() == "true"
        client_id = fields.get("client_id", "")
        client_secret = fields.get("client_secret", "")
        if not enabled and not client_id and not client_secret:
            continue  # blank, skip
        upsert_oauth_provider(db, OAuthProviderConfig(
            slug=slug,
            name=slug.title(),
            enabled=enabled,
            client_id=client_id,
            client_secret=client_secret,
            issuer_url="",
        ))


def _import_oidc_providers(db: Session, env: Mapping[str, str]) -> None:
    by_middle: dict[str, dict[str, str]] = {}
    for key, value in env.items():
        m = _OIDC_ENV_RE.match(key)
        if not m:
            continue
        middle, field = m.group(1), m.group(2).lower()
        by_middle.setdefault(middle, {})[field] = value

    for middle, fields in by_middle.items():
        slug = slug_from_env_middle(middle)
        if validate_slug(slug) is not None:
            continue  # invalid (e.g. starts with a digit, or reserved)
        if get_oauth_provider(db, slug) is not None:
            continue  # already configured by wizard / Config page
        enabled = fields.get("enabled", "false").lower() == "true"
        client_id = fields.get("client_id", "")
        client_secret = fields.get("client_secret", "")
        issuer_url = fields.get("issuer_url", "")
        upsert_oauth_provider(db, OAuthProviderConfig(
            slug=slug,
            name=fields.get("name", slug.title()),
            enabled=enabled,
            client_id=client_id,
            client_secret=client_secret,
            issuer_url=issuer_url,
        ))


def _import_smtp(db: Session, env: Mapping[str, str]) -> None:
    if get_smtp_config(db) is not None:
        return  # already configured

    fields: dict[str, str] = {}
    for env_key, smtp_key in _SMTP_KEYS.items():
        if env_key in env:
            fields[smtp_key] = env[env_key]

    if "host" not in fields:
        return  # SMTP not set in env

    try:
        port = int(fields.get("port", "587"))
    except ValueError:
        return  # corrupt env, skip rather than crash the migration

    upsert_smtp_config(db, SmtpConfig(
        host=fields["host"],
        port=port,
        username=fields.get("username", ""),
        password=fields.get("password", ""),
        from_address=fields.get("from_address", ""),
        use_tls=fields.get("use_tls", "false").lower() == "true",
    ))
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/pytest tests/test_env_import.py -q`
Expected: 8 passed.

### Step 4.3: Create the Alembic data migration

- [ ] **Step 5: Generate a migration**

Run:

```bash
nix-shell --run "alembic -c alembic.ini revision -m 'import env to app_config'"
```

Note the generated file path under `alembic/versions/`.

- [ ] **Step 6: Replace the body of the migration**

Open the new file. The `revision`, `down_revision`, `branch_labels`, and `depends_on` lines at the top stay as Alembic generated them. Replace the `upgrade()` and `downgrade()` bodies:

```python
"""import env to app_config

Revision ID: <auto-filled>
Revises: f62789139379
Create Date: <auto-filled>

"""
import os

from alembic import op
from sqlalchemy.orm import Session

# revision identifiers, used by Alembic.
revision = "<auto-filled>"
down_revision = "f62789139379"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from backend.config_mgmt.env_import import import_env_to_app_config

    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        import_env_to_app_config(session, dict(os.environ))
        session.commit()
    finally:
        session.close()


def downgrade() -> None:
    # No downgrade — env vars persist in the environment, so the rows can be
    # re-imported by re-running the upgrade. Removing oauth.* / smtp.* rows
    # here would discard wizard / Config-page edits made after the upgrade.
    pass
```

(Note: `import_env_to_app_config` already calls `mark_setup_completed`, which internally commits. The `session.commit()` is belt-and-suspenders for any future helper that doesn't commit.)

- [ ] **Step 7: Verify the migration upgrades cleanly against a real SQLite**

Run:

```bash
SKYNET_DATABASE_URL="sqlite:///$(mktemp -u --suffix=.db)" \
SKYNET_AUTH_GOOGLE__ENABLED=true \
SKYNET_AUTH_GOOGLE__CLIENT_ID=test-id \
SKYNET_AUTH_GOOGLE__CLIENT_SECRET=test-secret \
.venv/bin/alembic -c alembic.ini upgrade head
```

Then open the DB and check the row:

```bash
sqlite3 <the-temp-db-path> "SELECT key, value FROM app_config WHERE key LIKE 'oauth.google.%';"
```

Expected: rows present, `setup_completed=true` also present.

- [ ] **Step 8: Run the full suite + ruff**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check backend/ tests/"`
Expected: all green.

### Step 4.4: Commit

- [ ] **Step 9: Commit**

```bash
git add backend/config_mgmt/env_import.py alembic/versions/*_import_env_to_app_config.py \
        tests/test_env_import.py
git commit -m "feat(migration): import SKYNET_* env vars into app_config on upgrade

One-shot Alembic data migration that runs once on next upgrade. Scans
os.environ for SKYNET_AUTH_<provider>__*, SKYNET_AUTH_OIDC_<slug>_*,
and SKYNET_SMTP__* and writes the matching rows into app_config.

Idempotent — existing rows are never overwritten, so admin edits via
the (future) Config page survive a re-upgrade. If app_config contains
any rows after the import (whether just-imported or pre-existing),
setup_completed is set so the wizard does not relaunch.

Existing deployments that boot the new release will continue to work
unchanged: their env vars get rehydrated into the DB on first boot,
and Phase 2a's lazy read paths then resolve providers + SMTP from
those rows."
```

---

## Out of scope (handled in later phases)

- The Config-page UI surfaces for the new OAuth / SMTP groups, including the
  "Test sign-in" and "Send test email" buttons — **Phase 2b**.
- The `/admin/test/oauth/<slug>` and `/admin/test/smtp` HTTP endpoints —
  **Phase 2b**.
- Wizard SPA, `/setup` middleware, atomic first-boot commit — **Phase 3**.
- Recovery CLI, `admin_recovery_tokens` table, `/recovery` route — **Phase 4**.
- Removing the OAuth / SMTP fields from `Settings` and the `_gather_oidc_providers`
  validator — **Phase 5**. This phase intentionally leaves the env-parsing path
  alive so existing deployments stay bootable until they've upgraded through
  Phase 2a at least once.

This phase's success criterion: a fresh `nixos-rebuild switch` of an existing
deployment running env-var auth boots cleanly into the new release, with all
provider state and SMTP visible in `app_config` and `setup_completed=true`,
and zero observable behaviour change at the OAuth / login layer.
