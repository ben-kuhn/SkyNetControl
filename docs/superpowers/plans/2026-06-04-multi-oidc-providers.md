# Multi-OIDC Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the singleton `SKYNET_AUTH_OIDC_*` config with N slug-prefixed OIDC provider blocks, each with its own friendly name, issuer URL, and OAuth credentials. The wizard discovers them from env, lets the user add/edit/remove individually, and prints redirect URIs early and often.

**Architecture:** A shared slug-validation module (`backend/auth/oidc_slug.py`) used by both the backend config validator and the wizard. `Settings` grows a `model_validator(mode="before")` that scans `os.environ` for `SKYNET_AUTH_OIDC_<MIDDLE>_*` keys and builds a `list[OIDCProviderConfig]`. The static `PROVIDERS` dict in `auth/providers.py` becomes a `FIXED_PROVIDERS` dict plus a `build_providers(settings)` builder that adds dynamic OIDC entries. The wizard's `PROVIDERS` table keeps the five fixed providers plus a "Generic OIDC" template; OIDC discovery + add/edit reuse the existing `_configure_provider` flow with a per-entry slug.

**Tech Stack:** Python 3.12, Pydantic v2, pydantic-settings v2, pytest, prompt_toolkit.

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `backend/auth/oidc_slug.py` | `RESERVED_SLUGS`, `slugify`, `validate_slug`, `slug_from_env_middle`, `env_middle_from_slug` — used by both backend config + wizard |
| `tests/test_oidc_slug.py` | Unit tests for the slug helpers |

**Modified files:**

| File | Change |
|------|--------|
| `backend/config.py` | Drop `auth_oidc` singleton, add `OIDCProviderConfig` model + `auth_oidc_providers: list[OIDCProviderConfig]` field + `@model_validator(mode="before")` |
| `backend/auth/providers.py` | Split `PROVIDERS` → `FIXED_PROVIDERS` + `build_providers(settings)`; update `get_enabled_providers` to include dynamic OIDC entries |
| `backend/auth/service.py` | `init_providers` calls `build_providers(settings)`; OIDC discovery URL is read from each enabled provider's `issuer_url` |
| `backend/cli/setup.py` | Add "Generic OIDC" as a template entry; new helpers `_oidc_providers_from_env`, updated `_enabled_providers`/`_disabled_providers`/`_configure_provider`/`step_oidc` (banner + recap) |
| `tests/test_auth_providers.py` | Rewrite OIDC tests to use new schema; add `build_providers` tests |
| `tests/test_auth_service_discovery.py` | Rewrite OIDC test to use new schema |
| `tests/test_setup.py` | Add tests for OIDC env discovery in wizard |
| `docs/deployment/secrets.md` | Update OIDC row |
| `docs/deployment/oidc-providers.md` | Rewrite Generic OIDC section with multi-provider example |

---

## Conventions

- Tests run inside the project venv: `.venv/bin/pytest …`
- Pydantic v2 is in use; validators are `@model_validator(mode="before")` with `@classmethod`
- All new `prompt_toolkit` imports stay inside function bodies, never at module top (existing pattern in `backend/cli/setup.py`)
- Slug normalization is **uppercase env middle ↔ lowercase URL slug**, with `_` ↔ `-`:
  - `MY_IDP` ↔ `my-idp`
  - `AUTHENTIK` ↔ `authentik`

---

### Task 1: Shared slug helpers (TDD)

**Files:**
- Create: `backend/auth/oidc_slug.py`
- Create: `tests/test_oidc_slug.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_oidc_slug.py`:

```python
import pytest

from backend.auth import oidc_slug


@pytest.mark.parametrize("name,expected", [
    ("Authentik", "authentik"),
    ("My IdP!", "my-idp"),
    ("  spaces  ", "spaces"),
    ("Company SSO 2", "company-sso-2"),
    ("---weird---", "weird"),
])
def test_slugify(name: str, expected: str) -> None:
    assert oidc_slug.slugify(name) == expected


@pytest.mark.parametrize("slug", [
    "authentik", "company-sso", "my-idp", "a", "a1", "a-b-c", "x2",
])
def test_validate_slug_accepts_good(slug: str) -> None:
    assert oidc_slug.validate_slug(slug) is None


@pytest.mark.parametrize("slug,reason_fragment", [
    ("", "must be lowercase"),
    ("-foo", "must be lowercase"),
    ("foo-", "must be lowercase"),
    ("foo--bar", "must be lowercase"),
    ("FOO", "must be lowercase"),
    ("foo_bar", "must be lowercase"),
    ("foo bar", "must be lowercase"),
])
def test_validate_slug_rejects_malformed(slug: str, reason_fragment: str) -> None:
    err = oidc_slug.validate_slug(slug)
    assert err is not None
    assert reason_fragment in err


@pytest.mark.parametrize("slug", ["google", "github", "microsoft", "discord", "facebook", "oidc"])
def test_validate_slug_rejects_reserved(slug: str) -> None:
    err = oidc_slug.validate_slug(slug)
    assert err is not None
    assert "reserved" in err


@pytest.mark.parametrize("middle,slug", [
    ("AUTHENTIK", "authentik"),
    ("MY_IDP", "my-idp"),
    ("COMPANY_SSO_2", "company-sso-2"),
])
def test_slug_from_env_middle(middle: str, slug: str) -> None:
    assert oidc_slug.slug_from_env_middle(middle) == slug


@pytest.mark.parametrize("slug,middle", [
    ("authentik", "AUTHENTIK"),
    ("my-idp", "MY_IDP"),
    ("company-sso-2", "COMPANY_SSO_2"),
])
def test_env_middle_from_slug(slug: str, middle: str) -> None:
    assert oidc_slug.env_middle_from_slug(slug) == middle
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/test_oidc_slug.py -v
```

Expected: ImportError or ModuleNotFoundError for `backend.auth.oidc_slug`.

- [ ] **Step 3: Implement the module**

Create `backend/auth/oidc_slug.py`:

```python
"""Shared OIDC slug validation + env-middle conversion.

Used by both the backend Settings validator and the setup wizard so the
rules can't drift between them.
"""

from __future__ import annotations

import re

RESERVED_SLUGS: frozenset[str] = frozenset({
    "google", "github", "microsoft", "discord", "facebook", "oidc",
})

_SLUG_OK = re.compile(r"^[a-z0-9](-?[a-z0-9])*$")


def slugify(name: str) -> str:
    """Convert a friendly name into a URL slug. Non-alphanumeric runs become dashes."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s


def validate_slug(slug: str) -> str | None:
    """Return None if slug is valid, else a human-readable error message."""
    if not _SLUG_OK.match(slug):
        return (
            "must be lowercase letters, digits, and single dashes between groups "
            "(no leading/trailing dash, no consecutive dashes)"
        )
    if slug in RESERVED_SLUGS:
        return f"'{slug}' is reserved; pick a different slug"
    return None


def slug_from_env_middle(middle: str) -> str:
    """Convert the captured middle of an env var name to a URL slug."""
    return middle.lower().replace("_", "-")


def env_middle_from_slug(slug: str) -> str:
    """Inverse of slug_from_env_middle — used when writing env vars."""
    return slug.upper().replace("-", "_")
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_oidc_slug.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/auth/oidc_slug.py tests/test_oidc_slug.py
git commit -m "feat(auth): shared OIDC slug validation + env-middle conversion"
```

---

### Task 2: OIDCProviderConfig model + Settings validator (TDD)

**Files:**
- Modify: `backend/config.py`
- Create: `tests/test_config_oidc.py`

This task introduces the new schema *alongside* the existing singleton — that lets us add tests without breaking pre-existing tests in the same commit. The singleton is removed in Task 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_oidc.py`:

```python
import pytest
from pydantic import ValidationError

from backend.config import Settings


def test_no_oidc_env_means_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # Strip any SKYNET_AUTH_OIDC_* the host environment might have set.
    for key in list(os_environ_keys()):
        if key.startswith("SKYNET_AUTH_OIDC_") and key not in {
            "SKYNET_AUTH_OIDC_ENABLED",
            "SKYNET_AUTH_OIDC_CLIENT_ID",
            "SKYNET_AUTH_OIDC_CLIENT_SECRET",
            "SKYNET_AUTH_OIDC_ISSUER_URL",
        }:
            monkeypatch.delenv(key, raising=False)
    settings = Settings(database_url="sqlite:///")
    assert settings.auth_oidc_providers == []


def os_environ_keys():
    import os
    return list(os.environ.keys())


def test_oidc_providers_parsed_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_ENABLED", "true")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_NAME", "Authentik")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_CLIENT_ID", "client-a")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_CLIENT_SECRET", "secret-a")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_ISSUER_URL", "https://idp.example.com")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_ENABLED", "false")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_NAME", "Keycloak")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_CLIENT_ID", "client-k")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_CLIENT_SECRET", "secret-k")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_ISSUER_URL", "https://kc.example.com")

    settings = Settings(database_url="sqlite:///")

    by_slug = {p.slug: p for p in settings.auth_oidc_providers}
    assert set(by_slug) == {"authentik", "keycloak"}
    a = by_slug["authentik"]
    assert a.name == "Authentik"
    assert a.enabled is True
    assert a.client_id == "client-a"
    assert a.client_secret == "secret-a"
    assert a.issuer_url == "https://idp.example.com"
    k = by_slug["keycloak"]
    assert k.enabled is False


def test_oidc_provider_missing_name_defaults_to_titlecased_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_ISSUER_URL", "https://idp.example.com")
    settings = Settings(database_url="sqlite:///")
    by_slug = {p.slug: p for p in settings.auth_oidc_providers}
    assert by_slug["authentik"].name == "Authentik"


def test_oidc_dashed_slug_via_underscored_env_middle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYNET_AUTH_OIDC_MY_IDP_ENABLED", "true")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_MY_IDP_CLIENT_ID", "x")
    settings = Settings(database_url="sqlite:///")
    slugs = [p.slug for p in settings.auth_oidc_providers]
    assert "my-idp" in slugs


@pytest.mark.parametrize("reserved_middle", ["GOOGLE", "GITHUB", "MICROSOFT", "DISCORD", "FACEBOOK"])
def test_reserved_slug_rejected_at_startup(monkeypatch: pytest.MonkeyPatch, reserved_middle: str) -> None:
    monkeypatch.setenv(f"SKYNET_AUTH_OIDC_{reserved_middle}_ENABLED", "true")
    monkeypatch.setenv(f"SKYNET_AUTH_OIDC_{reserved_middle}_CLIENT_ID", "x")
    with pytest.raises(ValidationError) as exc_info:
        Settings(database_url="sqlite:///")
    msg = str(exc_info.value)
    assert "reserved" in msg
    assert f"SKYNET_AUTH_OIDC_{reserved_middle}_" in msg
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/test_config_oidc.py -v
```

Expected: tests fail because `auth_oidc_providers` doesn't exist yet on `Settings`.

- [ ] **Step 3: Update `backend/config.py`**

Replace the entire file with:

```python
import os
import re

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings

from backend.auth.oidc_slug import slug_from_env_middle, validate_slug


class ProviderSettings(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""


class OIDCProviderSettings(ProviderSettings):
    issuer_url: str = ""


class OIDCProviderConfig(BaseModel):
    slug: str
    name: str
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    issuer_url: str = ""


class SmtpSettings(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    from_address: str = ""


_OIDC_ENV_RE = re.compile(
    r"^SKYNET_AUTH_OIDC_([A-Z0-9_]+)_(NAME|ENABLED|CLIENT_ID|CLIENT_SECRET|ISSUER_URL)$"
)


class Settings(BaseSettings):
    database_url: str = "sqlite:///skynetcontrol.db"
    static_dir: str = "frontend/dist"
    debug: bool = False

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # App
    app_base_url: str = "http://localhost:8000"

    # Auth providers
    auth_google: ProviderSettings = ProviderSettings()
    auth_microsoft: ProviderSettings = ProviderSettings()
    auth_github: ProviderSettings = ProviderSettings()
    auth_discord: ProviderSettings = ProviderSettings()
    auth_facebook: ProviderSettings = ProviderSettings()
    auth_oidc: OIDCProviderSettings = OIDCProviderSettings()  # removed in Task 3
    auth_oidc_providers: list[OIDCProviderConfig] = []

    # SMTP
    smtp: SmtpSettings = SmtpSettings()

    model_config = {"env_prefix": "SKYNET_", "env_nested_delimiter": "_"}

    @model_validator(mode="before")
    @classmethod
    def _gather_oidc_providers(cls, data):
        # Scan os.environ and build auth_oidc_providers from SKYNET_AUTH_OIDC_*
        # env vars. Explicit kwarg `auth_oidc_providers=[...]` (used in tests)
        # always wins to keep tests deterministic regardless of host env state.
        if not isinstance(data, dict):
            return data
        if data.get("auth_oidc_providers"):
            return data
        groups: dict[str, dict[str, str]] = {}
        for key, value in os.environ.items():
            m = _OIDC_ENV_RE.match(key)
            if not m:
                continue
            middle, field = m.group(1), m.group(2)
            groups.setdefault(middle, {})[field.lower()] = value
        providers = []
        for middle in sorted(groups):
            slug = slug_from_env_middle(middle)
            err = validate_slug(slug)
            if err:
                raise ValueError(
                    f"Invalid OIDC slug derived from env var SKYNET_AUTH_OIDC_{middle}_*: {err}"
                )
            fields = groups[middle]
            providers.append({
                "slug": slug,
                "name": fields.get("name") or slug.title(),
                "enabled": fields.get("enabled", "false").lower() == "true",
                "client_id": fields.get("client_id", ""),
                "client_secret": fields.get("client_secret", ""),
                "issuer_url": fields.get("issuer_url", ""),
            })
        if providers:
            data["auth_oidc_providers"] = providers
        return data


settings = Settings()
```

- [ ] **Step 4: Run the new tests**

```bash
.venv/bin/pytest tests/test_config_oidc.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full suite to confirm no regressions**

```bash
.venv/bin/pytest -q
```

Expected: all pre-existing tests still pass (singleton `auth_oidc` is still in place).

- [ ] **Step 6: Commit**

```bash
git add backend/config.py tests/test_config_oidc.py
git commit -m "feat(config): OIDCProviderConfig + Settings validator for multi-OIDC env scan"
```

---

### Task 3: Drop the singleton `auth_oidc`

**Files:**
- Modify: `backend/config.py`
- Modify: `tests/test_auth_providers.py`
- Modify: `tests/test_auth_service_discovery.py`

The singleton stops being read by the validator (next task handles `get_enabled_providers`), so it's dead weight after this. Existing tests use kw-arg `auth_oidc=OIDCProviderSettings(...)` which must move to `auth_oidc_providers=[OIDCProviderConfig(...)]`.

- [ ] **Step 1: Update test fixtures in `tests/test_auth_providers.py`**

Find:

```python
from backend.config import Settings, ProviderSettings, OIDCProviderSettings
```

Replace with:

```python
from backend.config import Settings, ProviderSettings, OIDCProviderConfig
```

Find:

```python
def test_get_enabled_providers_oidc_enabled():
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc=OIDCProviderSettings(
            enabled=True, client_id="oid", client_secret="osec", issuer_url="https://idp.example.com"
        ),
    )
    result = get_enabled_providers(settings)
    assert "oidc" in result
    assert result["oidc"].client_id == "oid"
```

Replace with:

```python
def test_get_enabled_providers_oidc_enabled():
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik",
            enabled=True, client_id="oid", client_secret="osec",
            issuer_url="https://idp.example.com",
        )],
    )
    result = get_enabled_providers(settings)
    assert "authentik" in result
    assert result["authentik"].client_id == "oid"
```

- [ ] **Step 2: Update test fixtures in `tests/test_auth_service_discovery.py`**

Find:

```python
from backend.config import Settings, ProviderSettings, OIDCProviderSettings
```

Replace with:

```python
from backend.config import Settings, ProviderSettings, OIDCProviderConfig
```

Find:

```python
async def test_init_providers_with_generic_oidc():
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc=OIDCProviderSettings(
            enabled=True, client_id="oid", client_secret="osec", issuer_url="https://idp.example.com"
        ),
    )

    mock_discovery = {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
        "userinfo_endpoint": "https://idp.example.com/userinfo",
    }

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock, return_value=mock_discovery):
        providers = await init_providers(settings)

    assert "oidc" in providers
    assert providers["oidc"]["authorize_url"] == "https://idp.example.com/authorize"
```

Replace with:

```python
async def test_init_providers_with_generic_oidc():
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik",
            enabled=True, client_id="oid", client_secret="osec",
            issuer_url="https://idp.example.com",
        )],
    )

    mock_discovery = {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
        "userinfo_endpoint": "https://idp.example.com/userinfo",
    }

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock, return_value=mock_discovery):
        providers = await init_providers(settings)

    assert "authentik" in providers
    assert providers["authentik"]["authorize_url"] == "https://idp.example.com/authorize"
```

- [ ] **Step 3: Remove the singleton + dead model from `backend/config.py`**

Open `backend/config.py`. Delete the `class OIDCProviderSettings(ProviderSettings)` block. Delete the `auth_oidc: OIDCProviderSettings = OIDCProviderSettings()` line.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest -q
```

Expected: tests for `auth_oidc_providers` still pass; updated tests in `test_auth_providers.py` and `test_auth_service_discovery.py` will FAIL because `get_enabled_providers` and `init_providers` don't know about `auth_oidc_providers` yet — that's Tasks 4 and 5. **Two specific failures here are expected and acceptable for this commit:** `test_get_enabled_providers_oidc_enabled` and `test_init_providers_with_generic_oidc`.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/test_auth_providers.py tests/test_auth_service_discovery.py
git commit -m "refactor(config): drop singleton auth_oidc — replaced by auth_oidc_providers"
```

---

### Task 4: Refactor provider registry (`backend/auth/providers.py`)

**Files:**
- Modify: `backend/auth/providers.py`
- Modify: `tests/test_auth_providers.py`

- [ ] **Step 1: Write failing tests for `build_providers`**

Append to `tests/test_auth_providers.py`:

```python
def test_build_providers_returns_fixed_five_when_no_oidc() -> None:
    from backend.auth.providers import build_providers
    settings = Settings(database_url="sqlite:///")
    providers = build_providers(settings)
    assert set(providers) == {"google", "microsoft", "github", "discord", "facebook"}


def test_build_providers_adds_dynamic_oidc_entry() -> None:
    from backend.auth.providers import build_providers
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik",
            enabled=True, client_id="x", client_secret="y",
            issuer_url="https://idp.example.com",
        )],
    )
    providers = build_providers(settings)
    assert "authentik" in providers
    assert providers["authentik"].label == "Authentik"
    assert providers["authentik"].protocol == "oidc"
    assert providers["authentik"].discovery_url == "https://idp.example.com/.well-known/openid-configuration"


def test_build_providers_still_adds_disabled_oidc_to_registry() -> None:
    # The registry holds discovery info; enabled-ness is filtered separately
    # by get_enabled_providers. So a disabled OIDC provider still appears in
    # build_providers' result.
    from backend.auth.providers import build_providers
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik",
            enabled=False, issuer_url="https://idp.example.com",
        )],
    )
    providers = build_providers(settings)
    assert "authentik" in providers


def test_get_enabled_providers_excludes_disabled_oidc() -> None:
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik", enabled=False,
        )],
    )
    result = get_enabled_providers(settings)
    assert "authentik" not in result


def test_get_enabled_providers_multiple_oidc() -> None:
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[
            OIDCProviderConfig(slug="authentik", name="Authentik", enabled=True),
            OIDCProviderConfig(slug="keycloak", name="Keycloak", enabled=True),
        ],
    )
    result = get_enabled_providers(settings)
    assert "authentik" in result and "keycloak" in result


def test_normalise_issuer_appends_path_if_missing() -> None:
    from backend.auth.providers import _normalise_issuer
    assert _normalise_issuer("https://idp.example.com") == "https://idp.example.com/.well-known/openid-configuration"
    assert _normalise_issuer("https://idp.example.com/") == "https://idp.example.com/.well-known/openid-configuration"


def test_normalise_issuer_idempotent() -> None:
    from backend.auth.providers import _normalise_issuer
    full = "https://idp.example.com/.well-known/openid-configuration"
    assert _normalise_issuer(full) == full
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/test_auth_providers.py -v
```

Expected: new tests fail because `build_providers` and `_normalise_issuer` don't exist; also the two pre-existing failures from Task 3 (`test_get_enabled_providers_oidc_enabled`) should now pass through this task's implementation.

- [ ] **Step 3: Rewrite `backend/auth/providers.py`**

Replace the file with:

```python
from dataclasses import dataclass, field
from typing import Callable

from backend.config import Settings, ProviderSettings, OIDCProviderConfig


@dataclass
class ProviderConfig:
    protocol: str  # "oidc" or "oauth2"
    label: str
    scopes: str
    # For OIDC providers, discovery_url is used to fetch endpoints at startup.
    # For OAuth2 providers, authorize/token/userinfo URLs are hardcoded.
    discovery_url: str = ""
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    extract_subject: Callable[[dict], str] = field(default=lambda: (lambda d: ""))
    extract_name: Callable[[dict], str] = field(default=lambda: (lambda d: ""))
    extract_email: Callable[[dict], str] = field(default=lambda: (lambda d: ""))


def _oidc_extract_subject(data: dict) -> str:
    return str(data.get("sub", ""))


def _oidc_extract_name(data: dict) -> str:
    return data.get("name", data.get("preferred_username", "Unknown"))


def _oidc_extract_email(data: dict) -> str:
    return data.get("email", "")


def _github_extract_subject(data: dict) -> str:
    return str(data.get("id", ""))


def _github_extract_name(data: dict) -> str:
    return data.get("name", data.get("login", "Unknown"))


def _github_extract_email(data: dict) -> str:
    return data.get("email", "")


def _discord_extract_subject(data: dict) -> str:
    return str(data.get("id", ""))


def _discord_extract_name(data: dict) -> str:
    return data.get("username", "Unknown")


def _discord_extract_email(data: dict) -> str:
    return data.get("email", "")


def _facebook_extract_subject(data: dict) -> str:
    return str(data.get("id", ""))


def _facebook_extract_name(data: dict) -> str:
    return data.get("name", "Unknown")


def _facebook_extract_email(data: dict) -> str:
    return data.get("email", "")


FIXED_PROVIDERS: dict[str, ProviderConfig] = {
    "google": ProviderConfig(
        protocol="oidc",
        label="Google",
        scopes="openid email profile",
        discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        extract_subject=_oidc_extract_subject,
        extract_name=_oidc_extract_name,
        extract_email=_oidc_extract_email,
    ),
    "microsoft": ProviderConfig(
        protocol="oidc",
        label="Microsoft",
        scopes="openid email profile",
        discovery_url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
        extract_subject=_oidc_extract_subject,
        extract_name=_oidc_extract_name,
        extract_email=_oidc_extract_email,
    ),
    "github": ProviderConfig(
        protocol="oauth2",
        label="GitHub",
        scopes="read:user user:email",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        extract_subject=_github_extract_subject,
        extract_name=_github_extract_name,
        extract_email=_github_extract_email,
    ),
    "discord": ProviderConfig(
        protocol="oauth2",
        label="Discord",
        scopes="identify email",
        authorize_url="https://discord.com/api/oauth2/authorize",
        token_url="https://discord.com/api/oauth2/token",
        userinfo_url="https://discord.com/api/users/@me",
        extract_subject=_discord_extract_subject,
        extract_name=_discord_extract_name,
        extract_email=_discord_extract_email,
    ),
    "facebook": ProviderConfig(
        protocol="oauth2",
        label="Facebook",
        scopes="public_profile email",
        authorize_url="https://www.facebook.com/v19.0/dialog/oauth",
        token_url="https://graph.facebook.com/v19.0/oauth/access_token",
        userinfo_url="https://graph.facebook.com/v19.0/me?fields=id,name,email",
        extract_subject=_facebook_extract_subject,
        extract_name=_facebook_extract_name,
        extract_email=_facebook_extract_email,
    ),
}


def _normalise_issuer(url: str) -> str:
    """Return the OIDC discovery URL — append /.well-known/openid-configuration if not already present."""
    url = url.rstrip("/")
    if url.endswith("/.well-known/openid-configuration"):
        return url
    return f"{url}/.well-known/openid-configuration"


def build_providers(settings: Settings) -> dict[str, ProviderConfig]:
    """Return all registered providers, combining fixed entries with the dynamic OIDC list."""
    result = dict(FIXED_PROVIDERS)
    for op in settings.auth_oidc_providers:
        result[op.slug] = ProviderConfig(
            protocol="oidc",
            label=op.name,
            scopes="openid email profile",
            discovery_url=_normalise_issuer(op.issuer_url) if op.issuer_url else "",
            extract_subject=_oidc_extract_subject,
            extract_name=_oidc_extract_name,
            extract_email=_oidc_extract_email,
        )
    return result


def get_enabled_providers(settings: Settings) -> dict[str, ProviderSettings | OIDCProviderConfig]:
    """Return enabled providers keyed by slug, with their per-provider credentials."""
    fixed: dict[str, ProviderSettings] = {
        "google": settings.auth_google,
        "microsoft": settings.auth_microsoft,
        "github": settings.auth_github,
        "discord": settings.auth_discord,
        "facebook": settings.auth_facebook,
    }
    enabled: dict[str, ProviderSettings | OIDCProviderConfig] = {
        name: ps for name, ps in fixed.items() if ps.enabled
    }
    for op in settings.auth_oidc_providers:
        if op.enabled:
            enabled[op.slug] = op
    return enabled
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_auth_providers.py -v
```

Expected: all PASS, including the test that was failing from Task 3.

- [ ] **Step 5: Run the full suite**

```bash
.venv/bin/pytest -q
```

Expected: `test_init_providers_with_generic_oidc` from `test_auth_service_discovery.py` is the only remaining failure (Task 5 fixes it).

- [ ] **Step 6: Commit**

```bash
git add backend/auth/providers.py tests/test_auth_providers.py
git commit -m "refactor(auth): build_providers() builds registry dynamically from settings"
```

---

### Task 5: Update `init_providers` (`backend/auth/service.py`)

**Files:**
- Modify: `backend/auth/service.py`

- [ ] **Step 1: Read the current implementation**

Read `backend/auth/service.py` lines 47–100 to refresh context on the function being changed.

- [ ] **Step 2: Update `init_providers`**

In `backend/auth/service.py`, find:

```python
from backend.auth.providers import PROVIDERS, get_enabled_providers
```

Replace with:

```python
from backend.auth.providers import build_providers, get_enabled_providers
```

Find:

```python
    resolved = {}
    for name, provider_settings in enabled.items():
        registry = PROVIDERS[name]

        if registry.protocol == "oidc":
            # Determine discovery URL
            if name == "oidc":
                discovery_url = f"{provider_settings.issuer_url}/.well-known/openid-configuration"
            else:
                discovery_url = registry.discovery_url
```

Replace with:

```python
    registry = build_providers(settings)
    resolved = {}
    for name, provider_settings in enabled.items():
        config = registry[name]

        if config.protocol == "oidc":
            # Dynamic OIDC entries already have discovery_url baked in by
            # build_providers; fixed OIDC entries (google, microsoft) too.
            discovery_url = config.discovery_url
```

Then in the same loop, find:

```python
        else:
            authorize_url = registry.authorize_url
            token_url = registry.token_url
            userinfo_url = registry.userinfo_url
```

Replace with:

```python
        else:
            authorize_url = config.authorize_url
            token_url = config.token_url
            userinfo_url = config.userinfo_url
```

And in the same loop, find:

```python
        resolved[name] = {
            "authorize_url": authorize_url,
            "token_url": token_url,
            "userinfo_url": userinfo_url,
            "client_id": provider_settings.client_id,
            "client_secret": provider_settings.client_secret,
            "scopes": registry.scopes,
            "label": registry.label,
            "protocol": registry.protocol,
            "extract_subject": registry.extract_subject,
            "extract_name": registry.extract_name,
            "extract_email": registry.extract_email,
        }
```

Replace with:

```python
        resolved[name] = {
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

- [ ] **Step 3: Add a multi-OIDC `init_providers` test**

Append to `tests/test_auth_service_discovery.py`:

```python
@pytest.mark.asyncio
async def test_init_providers_with_two_oidc():
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[
            OIDCProviderConfig(
                slug="authentik", name="Authentik", enabled=True,
                client_id="ca", client_secret="sa",
                issuer_url="https://a.example.com",
            ),
            OIDCProviderConfig(
                slug="keycloak", name="Keycloak", enabled=True,
                client_id="ck", client_secret="sk",
                issuer_url="https://k.example.com",
            ),
        ],
    )

    discoveries = {
        "https://a.example.com/.well-known/openid-configuration": {
            "authorization_endpoint": "https://a.example.com/authorize",
            "token_endpoint": "https://a.example.com/token",
            "userinfo_endpoint": "https://a.example.com/userinfo",
        },
        "https://k.example.com/.well-known/openid-configuration": {
            "authorization_endpoint": "https://k.example.com/authorize",
            "token_endpoint": "https://k.example.com/token",
            "userinfo_endpoint": "https://k.example.com/userinfo",
        },
    }

    async def fake_discover(url):
        return discoveries.get(url)

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock,
               side_effect=fake_discover):
        providers = await init_providers(settings)

    assert "authentik" in providers and "keycloak" in providers
    assert providers["authentik"]["authorize_url"] == "https://a.example.com/authorize"
    assert providers["keycloak"]["authorize_url"] == "https://k.example.com/authorize"
    assert providers["authentik"]["label"] == "Authentik"
    assert providers["keycloak"]["label"] == "Keycloak"
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_auth_service_discovery.py -v
```

Expected: all PASS (including the test from Task 3 that was waiting on this change).

- [ ] **Step 5: Full suite**

```bash
.venv/bin/pytest -q
```

Expected: full pass.

- [ ] **Step 6: Commit**

```bash
git add backend/auth/service.py tests/test_auth_service_discovery.py
git commit -m "feat(auth): init_providers consumes build_providers + per-provider issuer URLs"
```

---

### Task 6: Wizard — discover OIDC providers from env (TDD)

**Files:**
- Modify: `backend/cli/setup.py`
- Modify: `tests/test_setup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup.py`:

```python
def test_oidc_providers_from_env_groups_by_middle() -> None:
    env = {
        "SKYNET_AUTH_OIDC_AUTHENTIK_ENABLED": "true",
        "SKYNET_AUTH_OIDC_AUTHENTIK_NAME": "Authentik",
        "SKYNET_AUTH_OIDC_AUTHENTIK_CLIENT_ID": "x",
        "SKYNET_AUTH_OIDC_KEYCLOAK_ENABLED": "true",
        "SKYNET_AUTH_OIDC_KEYCLOAK_NAME": "Keycloak",
        "SKYNET_AUTH_OIDC_KEYCLOAK_CLIENT_ID": "y",
        "SKYNET_AUTH_GITHUB_ENABLED": "true",  # noise: not OIDC
    }
    descriptors = wizard._oidc_providers_from_env(env)
    by_slug = {d["slug"]: d for d in descriptors}
    assert set(by_slug) == {"authentik", "keycloak"}
    assert by_slug["authentik"]["name"] == "Authentik"
    assert by_slug["authentik"]["prefix"] == "SKYNET_AUTH_OIDC_AUTHENTIK_"
    assert by_slug["authentik"]["extra"] == ["ISSUER_URL"]
    assert by_slug["authentik"]["is_oidc"] is True


def test_oidc_providers_from_env_partial_provider() -> None:
    # Only ENABLED set — descriptor still returned (so user can edit/complete).
    env = {"SKYNET_AUTH_OIDC_AUTHENTIK_ENABLED": "true"}
    descriptors = wizard._oidc_providers_from_env(env)
    assert len(descriptors) == 1
    assert descriptors[0]["slug"] == "authentik"
    assert descriptors[0]["name"] == "Authentik"  # title-cased default


def test_oidc_providers_from_env_dashed_slug() -> None:
    env = {"SKYNET_AUTH_OIDC_MY_IDP_ENABLED": "true"}
    descriptors = wizard._oidc_providers_from_env(env)
    assert descriptors[0]["slug"] == "my-idp"
    assert descriptors[0]["prefix"] == "SKYNET_AUTH_OIDC_MY_IDP_"


def test_enabled_providers_includes_oidc() -> None:
    env = {
        "SKYNET_AUTH_OIDC_AUTHENTIK_ENABLED": "true",
        "SKYNET_AUTH_OIDC_AUTHENTIK_NAME": "Authentik",
    }
    enabled = wizard._enabled_providers(env)
    slugs = [p["slug"] for p in enabled]
    assert "authentik" in slugs


def test_disabled_providers_always_includes_generic_oidc() -> None:
    # Even with an OIDC provider already configured, the template stays available.
    env = {"SKYNET_AUTH_OIDC_AUTHENTIK_ENABLED": "true"}
    disabled = wizard._disabled_providers(env)
    names = [p["name"] for p in disabled]
    assert "Generic OIDC" in names
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/test_setup.py -v
```

Expected: `_oidc_providers_from_env` doesn't exist; `_enabled_providers` doesn't find OIDC entries; `_disabled_providers` excludes Generic OIDC when none enabled.

- [ ] **Step 3: Update `backend/cli/setup.py`**

Add `import re` near the top (if not already there).

Add a new constant + helper near the `PROVIDERS` table:

```python
_OIDC_ENV_RE = re.compile(
    r"^SKYNET_AUTH_OIDC_([A-Z0-9_]+)_(NAME|ENABLED|CLIENT_ID|CLIENT_SECRET|ISSUER_URL)$"
)


def _oidc_providers_from_env(env: dict[str, str]) -> list[dict]:
    """Return one descriptor per OIDC provider present in env.

    Each descriptor mirrors a PROVIDERS entry but with `slug`, `prefix`,
    `is_oidc=True`. NAME defaults to title-cased slug when absent.
    """
    from backend.auth.oidc_slug import slug_from_env_middle  # local: avoid cycle

    seen: dict[str, str] = {}  # slug -> env middle (e.g. "AUTHENTIK")
    names: dict[str, str] = {}
    for key in env:
        m = _OIDC_ENV_RE.match(key)
        if not m:
            continue
        middle = m.group(1)
        slug = slug_from_env_middle(middle)
        seen.setdefault(slug, middle)
        if m.group(2) == "NAME":
            names[slug] = env[key]

    descriptors = []
    for slug in sorted(seen):
        middle = seen[slug]
        descriptors.append({
            "name": names.get(slug, slug.title()),
            "slug": slug,
            "prefix": f"SKYNET_AUTH_OIDC_{middle}_",
            "extra": ["ISSUER_URL"],
            "console_url": "(your IdP's app-registration UI)",
            "is_oidc": True,
        })
    return descriptors
```

Mark the existing "Generic OIDC" entry in `PROVIDERS` as a template by adding `"is_template": True`:

Find:

```python
    {
        "name": "Generic OIDC",
        "prefix": "SKYNET_AUTH_OIDC_",
        "slug": "oidc",
        "extra": ["ISSUER_URL"],
        "console_url": "(your IdP's app-registration UI)",
    },
```

Replace with:

```python
    {
        "name": "Generic OIDC",
        "prefix": "SKYNET_AUTH_OIDC_",  # template only — real providers get a slugged prefix
        "slug": "oidc",
        "extra": ["ISSUER_URL"],
        "console_url": "(your IdP's app-registration UI)",
        "is_template": True,
    },
```

Replace `_enabled_providers` and `_disabled_providers`:

```python
def _enabled_providers(env: dict[str, str]) -> list[dict]:
    fixed = [p for p in PROVIDERS
             if not p.get("is_template")
             and env.get(f"{p['prefix']}ENABLED") == "true"]
    oidc = [p for p in _oidc_providers_from_env(env)
            if env.get(f"{p['prefix']}ENABLED") == "true"]
    return fixed + oidc


def _disabled_providers(env: dict[str, str]) -> list[dict]:
    enabled_slugs = {p["slug"] for p in _enabled_providers(env)}
    out = []
    for p in PROVIDERS:
        if p.get("is_template"):
            out.append(p)  # always available to add another OIDC
        elif p["slug"] not in enabled_slugs:
            out.append(p)
    return out
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_setup.py -v
```

Expected: all PASS, including pre-existing tests (the `is_template` flag is additive and the template still has its old fields).

- [ ] **Step 5: Commit**

```bash
git add backend/cli/setup.py tests/test_setup.py
git commit -m "feat(setup): discover OIDC providers from env, list them in wizard menus"
```

---

### Task 7: Wizard — multi-OIDC configure flow

**Files:**
- Modify: `backend/cli/setup.py`

This task touches `_configure_provider` (interactive flow, not auto-tested) and reuses the slug helpers from Task 1.

- [ ] **Step 1: Add the OIDC template branch to `_configure_provider`**

In `backend/cli/setup.py`, replace the entire `_configure_provider` function with:

```python
def _configure_provider(provider: dict, env: dict[str, str]) -> None:
    """Prompt for one provider's credentials and write them into env.

    For the Generic OIDC template (is_template=True), this adds a *new*
    OIDC provider: prompts for a friendly name, derives a slug, then
    re-dispatches to itself with the new provider descriptor.
    """
    from prompt_toolkit import prompt
    from prompt_toolkit.formatted_text import HTML

    from backend.auth.oidc_slug import (
        env_middle_from_slug, slugify, validate_slug,
    )

    if provider.get("is_template"):
        # Step 1: friendly name
        while True:
            name = prompt(HTML("\n  Friendly name for this OIDC provider "
                               "(e.g. <ansigreen>Authentik</ansigreen>): ")).strip()
            if name:
                break
            print("  Name is required.")

        # Step 2: slug — default from name, editable, validated, unique
        existing_slugs = {p["slug"] for p in _oidc_providers_from_env(env)}
        default_slug = slugify(name)
        while True:
            slug = prompt(HTML(f"  URL slug [<ansigreen>{default_slug}</ansigreen>]: ")).strip() or default_slug
            err = validate_slug(slug)
            if err is None and slug in existing_slugs:
                err = f"'{slug}' is already configured — pick another or [r]emove the existing one first"
            if err is None:
                break
            print(f"  {err}")
            default_slug = slug  # let user edit their own input next round

        # Now act as if we were configuring a discovered OIDC provider.
        new_descriptor = {
            "name": name,
            "slug": slug,
            "prefix": f"SKYNET_AUTH_OIDC_{env_middle_from_slug(slug)}_",
            "extra": ["ISSUER_URL"],
            "console_url": "(your IdP's app-registration UI)",
            "is_oidc": True,
        }
        env[f"{new_descriptor['prefix']}NAME"] = name
        _configure_provider(new_descriptor, env)
        return

    # Existing OIDC provider OR fixed provider — same flow.
    prefix = provider["prefix"]
    base_url = env.get("SKYNET_APP_BASE_URL", "http://localhost:8000").rstrip("/")
    redirect_uri = f"{base_url}/api/auth/callback/{provider['slug']}"

    print(f"\n  Configuring {provider['name']}")
    print(f"  Developer console: {provider['console_url']}")
    print(f"  Set the OAuth redirect / callback URL there to:")
    print(f"    {redirect_uri}")

    # OIDC providers (not templates): allow renaming.
    if provider.get("is_oidc"):
        cur_name = env.get(f"{prefix}NAME", provider["name"])
        new_name = prompt(
            HTML(f"  Friendly name [<ansigreen>{cur_name}</ansigreen>]: "),
        ).strip() or cur_name
        env[f"{prefix}NAME"] = new_name

    cur_id = env.get(f"{prefix}CLIENT_ID", "")
    cur_secret = env.get(f"{prefix}CLIENT_SECRET", "")

    id_hint = cur_id or "(not set)"
    new_id = prompt(HTML(f"  Client ID [<ansigreen>{id_hint}</ansigreen>]: ")).strip() or cur_id
    new_secret = prompt(
        HTML(f"  Client secret [<ansigreen>{_masked(cur_secret)}</ansigreen>]: "),
        is_password=True,
    ).strip() or cur_secret

    env[f"{prefix}ENABLED"] = "true"
    env[f"{prefix}CLIENT_ID"] = new_id
    env[f"{prefix}CLIENT_SECRET"] = new_secret

    for extra in provider["extra"]:
        cur_extra = env.get(f"{prefix}{extra}", "")
        new_extra = prompt(
            HTML(f"  {extra} [<ansigreen>{cur_extra or '(not set)'}</ansigreen>]: "),
        ).strip() or cur_extra
        env[f"{prefix}{extra}"] = new_extra
```

- [ ] **Step 2: Sanity check the file still parses + tests still pass**

```bash
.venv/bin/python -c "from backend.cli import setup; print('ok')"
.venv/bin/pytest tests/test_setup.py -q
```

Expected: `ok` then green.

- [ ] **Step 3: Commit**

```bash
git add backend/cli/setup.py
git commit -m "feat(setup): _configure_provider handles OIDC template — adds new providers by slug"
```

---

### Task 8: Wizard — step_oidc banner + recap

**Files:**
- Modify: `backend/cli/setup.py`

- [ ] **Step 1: Update `step_oidc`**

In `backend/cli/setup.py`, find the `def step_oidc(env: dict[str, str]) -> None:` block. Replace its body with:

```python
def step_oidc(env: dict[str, str]) -> None:
    """Step 2: Add/edit/remove OIDC providers in a loop."""
    from prompt_toolkit import prompt

    base_url = env.get("SKYNET_APP_BASE_URL", "http://localhost:8000").rstrip("/")

    print("\n" + "=" * 60)
    print("Step 2/4: OIDC providers")
    print("=" * 60)
    print("  At least one provider must be enabled before the backend will start.")
    print("  Redirect URI pattern (configure these in each provider's developer console):")
    print(f"    {base_url}/api/auth/callback/<provider>")

    while True:
        enabled = _enabled_providers(env)
        if enabled:
            names = ", ".join(_enabled_label(p) for p in enabled)
        else:
            names = "none"
        print(f"\n  Currently enabled: {names}")
        action = prompt("  Action [a]dd / [e]dit / [r]emove / [d]one: ").strip().lower() or "d"

        if action == "d":
            if not enabled:
                confirm = prompt("  No providers enabled. Continue anyway? [y/N]: ").strip().lower()
                if confirm != "y":
                    continue
            _print_redirect_recap(enabled, base_url)
            return

        if action == "a":
            candidates = _disabled_providers(env)
            if not candidates:
                print("  All supported providers are already enabled.")
                continue
            idx = _choose([p["name"] for p in candidates], "Pick a provider to add")
            if idx is None:
                continue
            _configure_provider(candidates[idx], env)

        elif action == "e":
            if not enabled:
                print("  No providers to edit.")
                continue
            idx = _choose([_enabled_label(p) for p in enabled], "Pick a provider to edit")
            if idx is None:
                continue
            _configure_provider(enabled[idx], env)

        elif action == "r":
            if not enabled:
                print("  No providers to remove.")
                continue
            idx = _choose([_enabled_label(p) for p in enabled], "Pick a provider to remove")
            if idx is None:
                continue
            _remove_provider(enabled[idx], env)
            print(f"  Removed {_enabled_label(enabled[idx])}.")

        else:
            print(f"  Unknown action: {action!r}")
```

Add two helpers above `step_oidc`:

```python
def _enabled_label(provider: dict) -> str:
    """Display label for a provider in the wizard's enabled list."""
    if provider.get("is_oidc"):
        return f"{provider['name']} (oidc: {provider['slug']})"
    return provider["name"]


def _print_redirect_recap(enabled: list[dict], base_url: str) -> None:
    """Print a recap of redirect URIs for the currently-enabled providers."""
    if not enabled:
        return
    width = max(len(p["name"]) for p in enabled) + 2
    print("\n  Redirect URIs to configure in your provider consoles:")
    for p in enabled:
        label = (p["name"] + ":").ljust(width)
        print(f"    {label} {base_url}/api/auth/callback/{p['slug']}")
```

- [ ] **Step 2: Sanity check**

```bash
.venv/bin/python -c "from backend.cli import setup; print('ok')"
.venv/bin/pytest tests/test_setup.py -q
```

Expected: green.

- [ ] **Step 3: Manual smoke**

Run from a scratch dir to verify the new flow renders correctly:

```bash
mkdir -p /tmp/skynet-oidc-smoke && cd /tmp/skynet-oidc-smoke
rm -f skynetcontrol.env docker-compose.yml skynetcontrol.nix
printf 'https://net.example.org\na\n6\nAuthentik\n\nhttps://idp.example.com\nca\nsa\na\n6\nKeycloak\n\nhttps://kc.example.com\nck\nsk\nd\nn\n1\n\n\n' \
  | /home/ku0hn/dev/SkyNetControl/.venv/bin/skynetcontrol-setup 2>&1 \
  | grep -E "(Currently enabled|Redirect URIs|callback/authentik|callback/keycloak|Wrote)"
ls -l skynetcontrol.env docker-compose.yml
cat skynetcontrol.env | grep AUTH_OIDC
cd / && rm -rf /tmp/skynet-oidc-smoke
```

Expected: the recap appears, both `callback/authentik` and `callback/keycloak` URIs are printed, and the env file contains `SKYNET_AUTH_OIDC_AUTHENTIK_*` and `SKYNET_AUTH_OIDC_KEYCLOAK_*` keys.

- [ ] **Step 4: Commit**

```bash
git add backend/cli/setup.py
git commit -m "feat(setup): show redirect-URI banner + recap in step_oidc"
```

---

### Task 9: Update docs

**Files:**
- Modify: `docs/deployment/secrets.md`
- Modify: `docs/deployment/oidc-providers.md`

- [ ] **Step 1: Update secrets.md OIDC row**

In `docs/deployment/secrets.md`, find:

```
| Generic OIDC | `SKYNET_AUTH_OIDC_CLIENT_ID` | `SKYNET_AUTH_OIDC_CLIENT_SECRET` | Also set `SKYNET_AUTH_OIDC_ISSUER_URL` |
```

Replace with:

```
| Generic OIDC | `SKYNET_AUTH_OIDC_<SLUG>_CLIENT_ID` | `SKYNET_AUTH_OIDC_<SLUG>_CLIENT_SECRET` | Per provider, also set `_ENABLED`, `_ISSUER_URL`, `_NAME`. `<SLUG>` is uppercase + underscores in the env var; the URL slug uses lowercase + dashes (e.g. `MY_IDP` ↔ `my-idp`). Repeat for as many OIDC providers as you need. |
```

- [ ] **Step 2: Rewrite the Generic OIDC section in oidc-providers.md**

In `docs/deployment/oidc-providers.md`, find the `## Generic OIDC` section. Replace its body with:

```markdown
## Generic OIDC

For any OIDC-compliant provider that isn't listed above (Authentik, Keycloak, Okta, Auth0, Zitadel, …). You can configure multiple Generic OIDC providers — each gets its own URL slug, friendly name, issuer URL, and OAuth credentials.

### Env-var pattern

```
SKYNET_AUTH_OIDC_<MIDDLE>_ENABLED=true
SKYNET_AUTH_OIDC_<MIDDLE>_NAME=My Authentik
SKYNET_AUTH_OIDC_<MIDDLE>_CLIENT_ID=...
SKYNET_AUTH_OIDC_<MIDDLE>_CLIENT_SECRET=...
SKYNET_AUTH_OIDC_<MIDDLE>_ISSUER_URL=https://idp.example.com
```

- `<MIDDLE>` is uppercase letters/digits/underscores (e.g. `AUTHENTIK`, `MY_IDP`).
- The URL slug is the lowercase version with underscores → dashes (e.g. `MY_IDP` → `my-idp`).
- Reserved slugs (cannot be used): `google`, `github`, `microsoft`, `discord`, `facebook`, `oidc`.
- `NAME` is the label shown on the login button (e.g. "My Authentik"). Defaults to the title-cased slug if omitted.
- The callback URL to register in the IdP is `{APP_BASE_URL}/api/auth/callback/<slug>`.

### Example: Authentik + Keycloak side-by-side

```bash
# Authentik
SKYNET_AUTH_OIDC_AUTHENTIK_ENABLED=true
SKYNET_AUTH_OIDC_AUTHENTIK_NAME=Authentik
SKYNET_AUTH_OIDC_AUTHENTIK_CLIENT_ID=...
SKYNET_AUTH_OIDC_AUTHENTIK_CLIENT_SECRET=...
SKYNET_AUTH_OIDC_AUTHENTIK_ISSUER_URL=https://authentik.example.org/application/o/skynet/

# Keycloak
SKYNET_AUTH_OIDC_KEYCLOAK_ENABLED=true
SKYNET_AUTH_OIDC_KEYCLOAK_NAME=Keycloak
SKYNET_AUTH_OIDC_KEYCLOAK_CLIENT_ID=...
SKYNET_AUTH_OIDC_KEYCLOAK_CLIENT_SECRET=...
SKYNET_AUTH_OIDC_KEYCLOAK_ISSUER_URL=https://kc.example.org/realms/skynet
```

Callback URLs to register in each IdP, for `APP_BASE_URL=https://net.example.org`:

- Authentik: `https://net.example.org/api/auth/callback/authentik`
- Keycloak: `https://net.example.org/api/auth/callback/keycloak`

The setup wizard (`skynetcontrol-setup`) prompts for friendly name + slug per provider and prints the per-provider redirect URI as you go — recommended over editing env files by hand.

### Breaking change (2026-06-04)

Earlier deployments used bare `SKYNET_AUTH_OIDC_CLIENT_ID` / `_CLIENT_SECRET` / `_ISSUER_URL` / `_ENABLED` without a slug. Those env vars are no longer recognised. Move your config to the slug-prefixed form (e.g. wrap your existing creds as `SKYNET_AUTH_OIDC_SSO_*`).
```

- [ ] **Step 3: Commit**

```bash
git add docs/deployment/secrets.md docs/deployment/oidc-providers.md
git commit -m "docs: multi-OIDC env-var pattern + Authentik/Keycloak example"
```

---

### Task 10: End-to-end smoke

**Files:** none modified — verification only.

- [ ] **Step 1: Full pytest sweep**

```bash
.venv/bin/pytest -q
```

Expected: all pass.

- [ ] **Step 2: Backend boot with two OIDC providers**

```bash
mkdir -p /tmp/skynet-multi-oidc && cd /tmp/skynet-multi-oidc
SKYNET_JWT_SECRET_KEY=test \
SKYNET_AUTH_OIDC_AUTHENTIK_ENABLED=true \
SKYNET_AUTH_OIDC_AUTHENTIK_NAME=Authentik \
SKYNET_AUTH_OIDC_AUTHENTIK_CLIENT_ID=ca \
SKYNET_AUTH_OIDC_AUTHENTIK_CLIENT_SECRET=sa \
SKYNET_AUTH_OIDC_AUTHENTIK_ISSUER_URL=https://idp.example.com \
SKYNET_AUTH_OIDC_KEYCLOAK_ENABLED=true \
SKYNET_AUTH_OIDC_KEYCLOAK_NAME=Keycloak \
SKYNET_AUTH_OIDC_KEYCLOAK_CLIENT_ID=ck \
SKYNET_AUTH_OIDC_KEYCLOAK_CLIENT_SECRET=sk \
SKYNET_AUTH_OIDC_KEYCLOAK_ISSUER_URL=https://kc.example.com \
/home/ku0hn/dev/SkyNetControl/.venv/bin/python -c "
from backend.config import Settings
s = Settings(database_url='sqlite:///')
for p in s.auth_oidc_providers:
    print(p.slug, p.name, p.enabled, p.issuer_url)
"
cd / && rm -rf /tmp/skynet-multi-oidc
```

Expected output:

```
authentik Authentik True https://idp.example.com
keycloak Keycloak True https://kc.example.com
```

- [ ] **Step 3: Wizard smoke — add two OIDC providers, re-run pre-fill**

Run interactively (cannot script the full prompt_toolkit flow reliably):

```bash
mkdir -p /tmp/skynet-wizard-multi && cd /tmp/skynet-wizard-multi
/home/ku0hn/dev/SkyNetControl/.venv/bin/skynetcontrol-setup
```

Walk through:
- APP_BASE_URL: `https://net.example.org`
- OIDC: `a` → pick **Generic OIDC** → name `Authentik` → slug Enter (= `authentik`) → issuer `https://idp.example.com` → client ID `ca` → secret `sa`
- `a` → **Generic OIDC** → name `Keycloak` → slug Enter (= `keycloak`) → issuer `https://kc.example.com` → client ID `ck` → secret `sk`
- `d` (done) — verify recap shows both URIs
- SMTP: `n`
- Output: `1` (docker-compose), accept defaults

Verify:

```bash
grep AUTH_OIDC skynetcontrol.env
```

Expected: 10 lines — `_ENABLED/_NAME/_CLIENT_ID/_CLIENT_SECRET/_ISSUER_URL` for both AUTHENTIK and KEYCLOAK.

Then re-run the wizard in the same directory:

```bash
/home/ku0hn/dev/SkyNetControl/.venv/bin/skynetcontrol-setup
```

- APP_BASE_URL: Enter (kept)
- OIDC step opening message should say `Currently enabled: Authentik (oidc: authentik), Keycloak (oidc: keycloak)`.
- `d` → recap appears with both URIs.

Cleanup:

```bash
cd / && rm -rf /tmp/skynet-wizard-multi
```

- [ ] **Step 4: Final commit (if any fixes were needed)**

If steps 1–3 surfaced any issues and you fixed them, commit here. Otherwise:

```bash
git status
```

Expected: clean.

---

## Done

After Task 10 the multi-OIDC feature is shipped. Operators can configure any number of OIDC providers via env or via the wizard, each shows up on the login page with its own friendly name, and the wizard prints the right redirect URI early (banner), during configure (per-provider), and on exit (recap).
