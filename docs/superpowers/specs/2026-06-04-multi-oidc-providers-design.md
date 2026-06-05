# Multi-OIDC Providers — Design Spec

**Goal:** Let an operator configure any number of Generic OIDC providers (Authentik, Keycloak, multiple tenants, etc.) instead of being limited to one. Each gets its own URL slug, friendly name, issuer URL, and OAuth credentials. The wizard prompts for them one at a time and shows the redirect URI to paste into the IdP, early and often.

**Scope:** Backend `config.py` schema, `auth/providers.py` registry, wizard (`backend/cli/setup.py`), docs, and tests. No frontend changes — `LoginPage.tsx` already iterates whatever `/api/auth/providers` returns.

**Breaking change:** The singleton `SKYNET_AUTH_OIDC_{ENABLED,CLIENT_ID,CLIENT_SECRET,ISSUER_URL}` env vars stop being recognised. No migration code (no real users yet); the changelog will call this out.

---

## Slug rules

- Slug pattern: `^[a-z0-9](-?[a-z0-9])*$` (lowercase alphanumeric, dashes between groups, no leading/trailing/double dashes).
- Reserved slugs (rejected): `google`, `github`, `microsoft`, `discord`, `facebook`, `oidc`.
- Env-var ↔ slug mapping is bidirectional and unambiguous:
  - Slug `my-idp` ↔ env middle `MY_IDP` (dashes ↔ underscores; uppercase ↔ lowercase).
  - Slug `authentik` ↔ env middle `AUTHENTIK`.
- The redirect URI uses the slug verbatim: `{APP_BASE_URL}/api/auth/callback/{slug}`.

---

## Config schema (`backend/config.py`)

Drop `auth_oidc: OIDCProviderSettings`. Add:

```python
class OIDCProviderConfig(BaseModel):
    slug: str
    name: str
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    issuer_url: str = ""


class Settings(BaseSettings):
    # ... existing fixed providers stay as-is ...
    auth_oidc_providers: list[OIDCProviderConfig] = []
    # ... rest unchanged ...

    @model_validator(mode="before")
    @classmethod
    def _gather_oidc_providers(cls, data):
        # Implementation per "Validator behaviour" below.
        # Returns `data` with `data["auth_oidc_providers"]` set to the discovered list.
        return data
```

### Validator behaviour
- Scan `os.environ` (the validator runs at `Settings()` construction time, which is exactly when env vars matter).
- Regex: `^SKYNET_AUTH_OIDC_([A-Z0-9_]+)_(NAME|ENABLED|CLIENT_ID|CLIENT_SECRET|ISSUER_URL)$`. The captured middle must yield a valid slug after `lower().replace("_", "-")`.
- For each unique middle, build one `OIDCProviderConfig`. Missing optional fields default per the model; missing `NAME` defaults to title-cased slug.
- Reserved slug or malformed slug → raise `ValueError` with the offending env-var name. App startup fails fast.

`auth_oidc_providers` is not normally read from a single env var; the validator is the only writer. If someone sets `SKYNET_AUTH_OIDC_PROVIDERS` directly, pydantic will try to JSON-parse it — that path is undocumented and unsupported; the validator overrides regardless.

---

## Provider registry (`backend/auth/providers.py`)

Replace the module-level `PROVIDERS: dict[str, ProviderConfig]` with a builder:

```python
FIXED_PROVIDERS: dict[str, ProviderConfig] = {
    "google":    ProviderConfig(...),  # unchanged from current PROVIDERS[oidc] minus the
    "microsoft": ProviderConfig(...),  # `oidc` entry; just keep the five fixed ones
    "github":    ProviderConfig(...),
    "discord":   ProviderConfig(...),
    "facebook":  ProviderConfig(...),
}


def build_providers(settings: Settings) -> dict[str, ProviderConfig]:
    """Return registered providers, combining fixed entries with dynamic OIDC ones."""
    result = dict(FIXED_PROVIDERS)
    for op in settings.auth_oidc_providers:
        result[op.slug] = ProviderConfig(
            protocol="oidc",
            label=op.name,
            scopes="openid email profile",
            discovery_url=_normalise_issuer(op.issuer_url),
            extract_subject=_oidc_extract_subject,
            extract_name=_oidc_extract_name,
            extract_email=_oidc_extract_email,
        )
    return result


def _normalise_issuer(url: str) -> str:
    """Append /.well-known/openid-configuration if not already present."""
    url = url.rstrip("/")
    if url.endswith("/.well-known/openid-configuration"):
        return url
    return f"{url}/.well-known/openid-configuration"


def get_enabled_providers(settings: Settings) -> dict[str, ProviderSettings]:
    fixed = {
        "google":    settings.auth_google,
        "microsoft": settings.auth_microsoft,
        "github":    settings.auth_github,
        "discord":   settings.auth_discord,
        "facebook":  settings.auth_facebook,
    }
    enabled = {name: ps for name, ps in fixed.items() if ps.enabled}
    for op in settings.auth_oidc_providers:
        if op.enabled:
            # OIDCProviderConfig is a ProviderSettings shape-compatible (enabled,
            # client_id, client_secret) — return it directly. Callers that need
            # issuer_url have it on the same object.
            enabled[op.slug] = op
    return enabled
```

App startup (wherever it currently does `app.state.providers = PROVIDERS`) changes to `app.state.providers = build_providers(settings)`. The route layer is unchanged.

`OIDCProviderConfig` shares `enabled`, `client_id`, `client_secret` field names with `ProviderSettings`; any code path that currently consumes the singleton `settings.auth_oidc.issuer_url` reads the same field on the new model. Audit needed: anywhere `settings.auth_oidc.issuer_url` is referenced today gets replaced with a lookup against `enabled[slug].issuer_url`.

---

## Wizard (`backend/cli/setup.py`)

### Slugify helper

```python
import re

_SLUG_OK = re.compile(r"^[a-z0-9](-?[a-z0-9])*$")
RESERVED_SLUGS = {"google", "github", "microsoft", "discord", "facebook", "oidc"}


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s


def _validate_slug(slug: str) -> str | None:
    """Return None if valid, else an error message."""
    if not _SLUG_OK.match(slug):
        return "must be lowercase letters/digits/dashes, no leading or trailing dash"
    if slug in RESERVED_SLUGS:
        return f"'{slug}' is reserved; pick a different slug"
    return None
```

### OIDC discovery from env

```python
_OIDC_ENV_RE = re.compile(r"^SKYNET_AUTH_OIDC_([A-Z0-9_]+)_(NAME|ENABLED|CLIENT_ID|CLIENT_SECRET|ISSUER_URL)$")


def _oidc_providers_from_env(env: dict[str, str]) -> list[dict]:
    """Return one descriptor dict per OIDC provider present in env.

    Each descriptor has: slug, name, prefix (=SKYNET_AUTH_OIDC_<MIDDLE>_),
    extra=["ISSUER_URL"], console_url, is_oidc=True.
    """
    seen: dict[str, str] = {}  # slug -> middle
    for key in env:
        m = _OIDC_ENV_RE.match(key)
        if not m:
            continue
        middle = m.group(1)
        slug = middle.lower().replace("_", "-")
        seen.setdefault(slug, middle)
    descriptors = []
    for slug, middle in sorted(seen.items()):
        prefix = f"SKYNET_AUTH_OIDC_{middle}_"
        name = env.get(f"{prefix}NAME", slug.title())
        descriptors.append({
            "name": name,
            "slug": slug,
            "prefix": prefix,
            "extra": ["ISSUER_URL"],
            "console_url": "(your IdP's app-registration UI)",
            "is_oidc": True,
        })
    return descriptors
```

### Updated `_enabled_providers` and `_disabled_providers`

```python
def _enabled_providers(env: dict[str, str]) -> list[dict]:
    out = [p for p in PROVIDERS if env.get(f"{p['prefix']}ENABLED") == "true"]
    out += [p for p in _oidc_providers_from_env(env)
            if env.get(f"{p['prefix']}ENABLED") == "true"]
    return out


def _disabled_providers(env: dict[str, str]) -> list[dict]:
    enabled = {(p["slug"]) for p in _enabled_providers(env)}
    # Generic OIDC is always offered as an "add" option — every add of OIDC
    # creates a *new* provider slug, so the template never disables itself.
    return [p for p in PROVIDERS if p["slug"] not in enabled or p["name"] == "Generic OIDC"]
```

The fixed `PROVIDERS` table gains a `slug` field for each entry (already added in commit `f59f309`).

### Provider configure flow

`_configure_provider(provider, env, *, app_base_url)` (signature gains `app_base_url` so we don't re-derive in three places):

For fixed providers — unchanged from current behaviour (prints redirect URI, prompts for CLIENT_ID/SECRET).

For the "Generic OIDC" template:
1. Prompt: `Friendly name (e.g. "Authentik"):` — required, non-empty.
2. Derive: `default_slug = _slugify(name)`.
3. Prompt: `Slug for URL [<default_slug>]:` — empty keeps default. Validate via `_validate_slug` *and* uniqueness against already-configured slugs. Loop until valid.
4. Build `prefix = f"SKYNET_AUTH_OIDC_{slug.replace('-', '_').upper()}_"`.
5. Show the resulting redirect URI: `{app_base_url}/api/auth/callback/{slug}`.
6. Prompt: issuer URL, client ID, client secret.
7. Persist `{prefix}NAME`, `{prefix}ENABLED=true`, `{prefix}ISSUER_URL`, `{prefix}CLIENT_ID`, `{prefix}CLIENT_SECRET`.

For an existing OIDC provider (invoked from "edit"):
- Same prompts, slug not editable, name editable (changing `NAME` rewrites only that key).
- Pre-fill ID + masked secret + issuer URL.

### `step_oidc` flow

```
============================================================
Step 2/4: OIDC providers
============================================================
  At least one provider must be enabled before the backend will start.
  Redirect URI pattern (configure these in each provider's developer console):
    {app_base_url}/api/auth/callback/<provider>

  Currently enabled: <list>
  Action [a]dd / [e]dit / [r]emove / [d]one: _
```

Loop is unchanged in shape from the current implementation. Differences:
- `_enabled_providers` discovers OIDC entries via env scan.
- "Add" menu always includes "Generic OIDC" (so the user can add another).
- "Edit/remove" menus show OIDC entries with their friendly name and slug, e.g. `Authentik (oidc: authentik)`.
- After the user hits `d` (and there's at least one provider, or they confirmed "Continue anyway?"), print the recap:

```
  Redirect URIs to configure in your provider consoles:
    GitHub:    {app_base_url}/api/auth/callback/github
    Authentik: {app_base_url}/api/auth/callback/authentik
    Keycloak:  {app_base_url}/api/auth/callback/keycloak
```

Recap lists every *enabled* provider (fixed or OIDC), one per line, name-aligned. Prefix width is `max(len(name) for name in enabled) + 2`.

### Remove behaviour

`_remove_provider(provider, env)` deletes all keys with `provider["prefix"]`. For OIDC providers this nukes the NAME/ENABLED/CLIENT_ID/CLIENT_SECRET/ISSUER_URL set cleanly. Unchanged in body — works because the discovered prefix is correct.

---

## Tests

### Backend

`tests/test_config.py` (new file or extend existing):
- `test_oidc_providers_parsed_from_env` — set three providers via `monkeypatch.setenv`, instantiate `Settings()`, assert `auth_oidc_providers` has the three with correct fields.
- `test_oidc_provider_missing_name_uses_titlecased_slug` — only `ISSUER_URL` set, name defaults to e.g. `"Authentik"`.
- `test_reserved_slug_rejected_at_startup` — `SKYNET_AUTH_OIDC_GOOGLE_ENABLED=true` → `Settings()` raises `ValueError` mentioning the env key.
- `test_no_oidc_env_means_empty_list` — bare environment → `auth_oidc_providers == []`.

`tests/test_auth_providers.py` (extend):
- `test_build_providers_includes_dynamic_oidc` — settings with one OIDC provider, assert `build_providers(settings)["authentik"].label == "Authentik"` and discovery URL ends with `/.well-known/openid-configuration`.
- `test_build_providers_skips_disabled_oidc` — enabled=False → key absent from result.
- `test_normalise_issuer_idempotent` — appends path once, doesn't double-append if already present.

### Wizard

`tests/test_setup.py` (extend):
- `test_slugify_basic` — `"Authentik" -> "authentik"`, `"My IdP!" -> "my-idp"`, `"  spaces  " -> "spaces"`.
- `test_validate_slug_rejects_reserved` — every reserved slug returns an error.
- `test_validate_slug_rejects_malformed` — `""`, `"-foo"`, `"foo--bar"`, `"FOO"`, `"foo_bar"` all rejected.
- `test_oidc_providers_from_env_groups_by_middle` — env with two complete provider blocks → two descriptors with correct slug/prefix/name.
- `test_oidc_providers_from_env_handles_partial_provider` — env with only `ENABLED` for one slug → still returns the descriptor (so wizard can edit/complete it).
- `test_enabled_providers_includes_oidc` — env with `SKYNET_AUTH_OIDC_AUTHENTIK_ENABLED=true` → `_enabled_providers` returns Authentik in addition to any fixed ones.
- `test_disabled_providers_always_lists_generic_oidc` — even when an OIDC provider exists, "Generic OIDC" is offered as an add target.

No UI flow tests (consistent with existing wizard tests).

---

## Docs

### `docs/deployment/secrets.md`

Replace the row:

```
| Generic OIDC | `SKYNET_AUTH_OIDC_CLIENT_ID` | `SKYNET_AUTH_OIDC_CLIENT_SECRET` | Also set `SKYNET_AUTH_OIDC_ISSUER_URL` |
```

with:

```
| Generic OIDC (multi) | `SKYNET_AUTH_OIDC_<SLUG>_CLIENT_ID` | `SKYNET_AUTH_OIDC_<SLUG>_CLIENT_SECRET` | Per provider: `_ENABLED`, `_ISSUER_URL`, `_NAME`. `<SLUG>` is uppercase + underscores in the env var; the URL slug uses lowercase + dashes. |
```

### `docs/deployment/oidc-providers.md`

Rewrite the Generic OIDC section to show two providers configured side-by-side (Authentik + Keycloak), with a note that each provider's redirect URI uses its own slug.

---

## Migration / changelog

Add a "Breaking changes" line to whatever changelog mechanism exists (or a new entry in README):

> `SKYNET_AUTH_OIDC_*` (bare) is no longer recognised. Move your config to `SKYNET_AUTH_OIDC_<SLUG>_*` with a slug of your choosing (e.g. `SKYNET_AUTH_OIDC_SSO_*` to keep something close to the old `sso` label). Add `SKYNET_AUTH_OIDC_<SLUG>_NAME=...` for the login-button label.

The wizard's re-run path on an existing skynetcontrol.env using the old bare keys: they won't appear as enabled providers (since `_enabled_providers` only matches the slug-prefixed pattern), and the bare keys won't be touched by the wizard. The operator should manually clean them up or re-run with a fresh env. This is acceptable given no real users.
