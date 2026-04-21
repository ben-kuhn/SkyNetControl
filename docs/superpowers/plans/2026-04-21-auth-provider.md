# Auth Provider Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-provider OIDC auth with a multi-provider system (Google, Microsoft, GitHub, Discord, Facebook, Generic OIDC), add user registration with admin approval, PENDING role, callsign changes with approval, SMTP email notifications, and secrets management documentation.

**Architecture:** A provider registry maps provider names to OAuth2/OIDC configs. Settings use Pydantic nested models with `env_nested_delimiter`. New users start as PENDING, self-register with a validated callsign, and await admin approval. Email notifications fire on registration and approval via SMTP (fire-and-forget). Callsign changes also require admin approval.

**Tech Stack:** FastAPI, Authlib, python-jose, SQLAlchemy 2.0+, Alembic, httpx, smtplib, pydantic-settings

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/config.py` | `ProviderSettings`, `OIDCProviderSettings`, `SmtpSettings` nested models; replace single OIDC settings |
| `backend/auth/providers.py` | Provider registry — per-provider OAuth2/OIDC endpoint URLs, scopes, userinfo extraction functions |
| `backend/auth/models.py` | Add `PENDING` to `UserRole`, add `email` and `pending_callsign` fields to `User` |
| `backend/auth/dependencies.py` | Add `require_not_pending()` dependency |
| `backend/auth/service.py` | Add `fetch_oidc_discovery()`, `init_providers()` for app startup |
| `backend/auth/routes.py` | Rewrite: multi-provider login/callback, providers list, registration, callsign change/approval |
| `backend/auth/email.py` | SMTP sending, admin/user notification functions |
| `backend/app.py` | Call `init_providers()` on startup |
| `alembic/versions/XXXX_add_pending_role_email_callsign.py` | Add PENDING enum, `email` + `pending_callsign` columns |
| `docs/deployment/secrets.md` | Secrets management guide |
| `tests/test_auth_providers.py` | Tests for provider registry |
| `tests/test_auth_email.py` | Tests for email module |
| `tests/test_auth_models.py` | Update with PENDING role tests |
| `tests/test_auth_dependencies.py` | Update with require_not_pending tests |
| `tests/test_auth_routes.py` | Rewrite for multi-provider routes |
| `tests/test_auth_registration.py` | Tests for registration + callsign change flows |

---

### Task 1: Configuration + Provider Registry

**Files:**
- Modify: `backend/config.py`
- Create: `backend/auth/providers.py`
- Create: `tests/test_auth_providers.py`

- [ ] **Step 1: Write tests for provider registry**

Create `tests/test_auth_providers.py`:

```python
import pytest

from backend.auth.providers import PROVIDERS, ProviderConfig, get_enabled_providers
from backend.config import Settings, ProviderSettings, OIDCProviderSettings


def test_all_providers_defined():
    expected = {"google", "microsoft", "github", "discord", "facebook", "oidc"}
    assert set(PROVIDERS.keys()) == expected


def test_oidc_providers_have_discovery_url():
    for name, config in PROVIDERS.items():
        if config.protocol == "oidc":
            assert config.discovery_url or name == "oidc", f"{name} missing discovery_url"


def test_oauth2_providers_have_hardcoded_urls():
    for name, config in PROVIDERS.items():
        if config.protocol == "oauth2":
            assert config.authorize_url, f"{name} missing authorize_url"
            assert config.token_url, f"{name} missing token_url"
            assert config.userinfo_url, f"{name} missing userinfo_url"


def test_all_providers_have_extract_functions():
    for name, config in PROVIDERS.items():
        assert callable(config.extract_subject), f"{name} missing extract_subject"
        assert callable(config.extract_name), f"{name} missing extract_name"
        assert callable(config.extract_email), f"{name} missing extract_email"


def test_google_extract_subject():
    config = PROVIDERS["google"]
    assert config.extract_subject({"sub": "12345"}) == "12345"


def test_github_extract_subject():
    config = PROVIDERS["github"]
    assert config.extract_subject({"id": 42}) == "42"


def test_github_extract_name():
    config = PROVIDERS["github"]
    assert config.extract_name({"name": "Test User"}) == "Test User"
    assert config.extract_name({"login": "testuser"}) == "testuser"


def test_facebook_extract_subject():
    config = PROVIDERS["facebook"]
    assert config.extract_subject({"id": "999"}) == "999"


def test_discord_extract_subject():
    config = PROVIDERS["discord"]
    assert config.extract_subject({"id": "123456"}) == "123456"


def test_discord_extract_name():
    config = PROVIDERS["discord"]
    assert config.extract_name({"username": "testuser"}) == "testuser"


def test_get_enabled_providers_none_enabled():
    settings = Settings(database_url="sqlite:///")
    result = get_enabled_providers(settings)
    assert result == {}


def test_get_enabled_providers_google_enabled():
    settings = Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
    )
    result = get_enabled_providers(settings)
    assert "google" in result
    assert result["google"].client_id == "gid"


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

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_auth_providers.py -v" 2>&1 | tail -20`
Expected: ImportError — `backend.auth.providers` does not exist yet.

- [ ] **Step 3: Update `backend/config.py`**

Replace the entire file with:

```python
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class ProviderSettings(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""


class OIDCProviderSettings(ProviderSettings):
    issuer_url: str = ""


class SmtpSettings(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    from_address: str = ""


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
    auth_oidc: OIDCProviderSettings = OIDCProviderSettings()

    # SMTP
    smtp: SmtpSettings = SmtpSettings()

    model_config = {"env_prefix": "SKYNET_", "env_nested_delimiter": "_"}


settings = Settings()
```

Note: This removes the old `oidc_issuer_url`, `oidc_client_id`, `oidc_client_secret`, and `oidc_redirect_uri` fields. Existing tests that reference those will break and are fixed in Task 9.

- [ ] **Step 4: Create `backend/auth/providers.py`**

```python
from dataclasses import dataclass, field
from typing import Callable

from backend.config import Settings, ProviderSettings


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


PROVIDERS: dict[str, ProviderConfig] = {
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
    "oidc": ProviderConfig(
        protocol="oidc",
        label="SSO",
        scopes="openid email profile",
        # discovery_url is empty — populated from settings.auth_oidc.issuer_url at runtime
        extract_subject=_oidc_extract_subject,
        extract_name=_oidc_extract_name,
        extract_email=_oidc_extract_email,
    ),
}


def get_enabled_providers(settings: Settings) -> dict[str, ProviderSettings]:
    """Return a dict of provider_name -> ProviderSettings for all enabled providers."""
    mapping = {
        "google": settings.auth_google,
        "microsoft": settings.auth_microsoft,
        "github": settings.auth_github,
        "discord": settings.auth_discord,
        "facebook": settings.auth_facebook,
        "oidc": settings.auth_oidc,
    }
    return {name: ps for name, ps in mapping.items() if ps.enabled}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_auth_providers.py -v" 2>&1 | tail -25`
Expected: All 13 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/auth/providers.py tests/test_auth_providers.py
git commit -m "feat: add multi-provider config and provider registry"
```

---

### Task 2: User Model + Migration

**Files:**
- Modify: `backend/auth/models.py`
- Create: `alembic/versions/XXXX_add_pending_role_email_callsign.py`
- Modify: `tests/test_auth_models.py`

- [ ] **Step 1: Write tests for new model fields**

Append to `tests/test_auth_models.py`:

```python
def test_pending_role_exists():
    assert UserRole.PENDING.value == "pending"


def test_user_email_nullable(app):
    with app.state.session_factory() as session:
        user = User(
            callsign="W0TST",
            oidc_subject="test|email",
            name="Test",
            role=UserRole.VIEWER,
            email="test@example.com",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.email == "test@example.com"


def test_user_email_defaults_none(app):
    with app.state.session_factory() as session:
        user = User(
            callsign="W0NOE",
            oidc_subject="test|noemail",
            name="No Email",
            role=UserRole.VIEWER,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.email is None


def test_user_pending_callsign(app):
    with app.state.session_factory() as session:
        user = User(
            callsign="W0OLD",
            oidc_subject="test|pending_cs",
            name="Pending CS",
            role=UserRole.VIEWER,
            pending_callsign="W0NEW",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.pending_callsign == "W0NEW"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_auth_models.py -v" 2>&1 | tail -20`
Expected: Failures — `PENDING` not in `UserRole`, `email` and `pending_callsign` not on `User`.

- [ ] **Step 3: Update `backend/auth/models.py`**

Replace the entire file:

```python
import enum
from datetime import datetime, timezone

from sqlalchemy import String, Enum, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    NET_CONTROL = "net_control"
    VIEWER = "viewer"
    PENDING = "pending"


class User(Base):
    __tablename__ = "users"

    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    oidc_subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.VIEWER)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pending_callsign: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_auth_models.py -v" 2>&1 | tail -20`
Expected: All tests pass (old + new).

- [ ] **Step 5: Create the Alembic migration**

Run: `nix-shell --run "alembic revision -m 'add pending role email and pending callsign'"`

Then edit the generated file. The revision ID will vary — find it with:
`ls -t alembic/versions/ | head -1`

Replace the `upgrade()` and `downgrade()` functions:

```python
"""add pending role email and pending callsign

Revision ID: <generated>
Revises: f5b2383f6dd3
Create Date: <generated>
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "<generated>"
down_revision: Union[str, None] = "f5b2383f6dd3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to users table
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("pending_callsign", sa.String(20), nullable=True))

    # For SQLite: the enum is stored as VARCHAR, so adding PENDING just works.
    # For PostgreSQL, uncomment:
    # op.execute("ALTER TYPE userrole ADD VALUE 'pending'")


def downgrade() -> None:
    op.drop_column("users", "pending_callsign")
    op.drop_column("users", "email")
```

- [ ] **Step 6: Verify migration applies**

Run: `nix-shell --run "python -c \"from backend.auth.models import UserRole; print(UserRole.PENDING)\""`
Expected: `UserRole.PENDING`

- [ ] **Step 7: Commit**

```bash
git add backend/auth/models.py alembic/versions/ tests/test_auth_models.py
git commit -m "feat: add PENDING role, email, and pending_callsign to User model"
```

---

### Task 3: Auth Dependencies

**Files:**
- Modify: `backend/auth/dependencies.py`
- Modify: `tests/test_auth_dependencies.py`

- [ ] **Step 1: Write tests for `require_not_pending`**

Append to `tests/test_auth_dependencies.py`. First add the new import at the top of the file — add `require_not_pending` to the existing import line:

```python
from backend.auth.dependencies import get_current_user, require_role, require_not_pending
```

Add a PENDING user to the `seeded_db` fixture — after the `viewer` user creation, add:

```python
        pending = User(
            callsign="PENDING-abc123",
            oidc_subject="auth0|pending",
            name="Pending User",
            role=UserRole.PENDING,
        )
        session.add_all([admin, viewer, pending])
```

Add a `require_not_pending` route to the `test_app` fixture:

```python
    @app.get("/api/test/not-pending")
    async def not_pending(user: User = Depends(require_not_pending)):
        return {"callsign": user.callsign}
```

Then add these test functions:

```python
@pytest.mark.asyncio
async def test_pending_user_blocked_by_require_not_pending(test_client, test_settings):
    token = create_access_token("PENDING-abc123", "pending", test_settings)
    response = await test_client.get("/api/test/not-pending", cookies={"access_token": token})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_passes_require_not_pending(test_client, test_settings):
    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.get("/api/test/not-pending", cookies={"access_token": token})
    assert response.status_code == 200
    assert response.json()["callsign"] == "KD0TST"


@pytest.mark.asyncio
async def test_admin_passes_require_not_pending(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get("/api/test/not-pending", cookies={"access_token": token})
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_auth_dependencies.py -v" 2>&1 | tail -20`
Expected: ImportError — `require_not_pending` not found.

- [ ] **Step 3: Add `require_not_pending` to `backend/auth/dependencies.py`**

Add this function after the existing `require_role` function:

```python
def require_not_pending(user: User = Depends(get_current_user)) -> User:
    if user.role == UserRole.PENDING:
        raise HTTPException(status_code=403, detail="Account pending approval")
    return user
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_auth_dependencies.py -v" 2>&1 | tail -20`
Expected: All 7 tests pass (4 old + 3 new).

- [ ] **Step 5: Commit**

```bash
git add backend/auth/dependencies.py tests/test_auth_dependencies.py
git commit -m "feat: add require_not_pending dependency"
```

---

### Task 4: OIDC Discovery + Provider Initialization

**Files:**
- Modify: `backend/auth/service.py`
- Modify: `backend/app.py`
- Create: `tests/test_auth_service_discovery.py`

- [ ] **Step 1: Write tests for OIDC discovery and provider init**

Create `tests/test_auth_service_discovery.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from backend.auth.service import fetch_oidc_discovery, init_providers
from backend.auth.providers import PROVIDERS
from backend.config import Settings, ProviderSettings, OIDCProviderSettings


@pytest.mark.asyncio
async def test_fetch_oidc_discovery_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "authorization_endpoint": "https://example.com/authorize",
        "token_endpoint": "https://example.com/token",
        "userinfo_endpoint": "https://example.com/userinfo",
    }

    with patch("backend.auth.service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fetch_oidc_discovery("https://example.com/.well-known/openid-configuration")

    assert result["authorization_endpoint"] == "https://example.com/authorize"
    assert result["token_endpoint"] == "https://example.com/token"
    assert result["userinfo_endpoint"] == "https://example.com/userinfo"


@pytest.mark.asyncio
async def test_fetch_oidc_discovery_failure_returns_none():
    with patch("backend.auth.service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fetch_oidc_discovery("https://bad.example.com/.well-known/openid-configuration")

    assert result is None


@pytest.mark.asyncio
async def test_init_providers_with_google():
    settings = Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
    )

    mock_discovery = {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    }

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock, return_value=mock_discovery):
        providers = await init_providers(settings)

    assert "google" in providers
    assert providers["google"]["authorize_url"] == "https://accounts.google.com/o/oauth2/v2/auth"
    assert providers["google"]["client_id"] == "gid"
    assert providers["google"]["client_secret"] == "gsec"


@pytest.mark.asyncio
async def test_init_providers_with_github():
    settings = Settings(
        database_url="sqlite:///",
        auth_github=ProviderSettings(enabled=True, client_id="ghid", client_secret="ghsec"),
    )

    providers = await init_providers(settings)

    assert "github" in providers
    assert providers["github"]["authorize_url"] == "https://github.com/login/oauth/authorize"
    assert providers["github"]["client_id"] == "ghid"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_init_providers_skips_failed_oidc_discovery():
    settings = Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
        auth_github=ProviderSettings(enabled=True, client_id="ghid", client_secret="ghsec"),
    )

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock, return_value=None):
        providers = await init_providers(settings)

    # Google (OIDC) should be skipped, GitHub (OAuth2) should still be there
    assert "google" not in providers
    assert "github" in providers


@pytest.mark.asyncio
async def test_init_providers_none_enabled_raises():
    settings = Settings(database_url="sqlite:///")

    with pytest.raises(RuntimeError, match="No auth providers"):
        await init_providers(settings)


@pytest.mark.asyncio
async def test_init_providers_all_fail_raises():
    settings = Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
    )

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock, return_value=None):
        with pytest.raises(RuntimeError, match="No auth providers"):
            await init_providers(settings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_auth_service_discovery.py -v" 2>&1 | tail -20`
Expected: ImportError — `fetch_oidc_discovery` and `init_providers` not found.

- [ ] **Step 3: Update `backend/auth/service.py`**

Replace the entire file:

```python
import logging
from datetime import datetime, timedelta, timezone

import httpx
from jose import JWTError, jwt

from backend.auth.providers import PROVIDERS, get_enabled_providers
from backend.config import Settings

logger = logging.getLogger(__name__)


def create_access_token(
    callsign: str,
    role: str,
    settings: Settings,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": callsign,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None


async def fetch_oidc_discovery(discovery_url: str) -> dict | None:
    """Fetch OIDC discovery document and return endpoint URLs, or None on failure."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(discovery_url, timeout=10)
            response.raise_for_status()
            return response.json()
    except Exception:
        logger.error("Failed to fetch OIDC discovery from %s", discovery_url)
        return None


async def init_providers(settings: Settings) -> dict[str, dict]:
    """Initialize all enabled auth providers. Returns dict of provider_name -> resolved config.

    Each resolved config contains: authorize_url, token_url, userinfo_url,
    client_id, client_secret, scopes, label, protocol, and the provider's
    extract_* functions from the registry.

    Raises RuntimeError if no providers could be initialized.
    """
    enabled = get_enabled_providers(settings)
    if not enabled:
        raise RuntimeError("No auth providers are enabled. Set at least one SKYNET_AUTH_*_ENABLED=true.")

    resolved = {}
    for name, provider_settings in enabled.items():
        registry = PROVIDERS[name]

        if registry.protocol == "oidc":
            # Determine discovery URL
            if name == "oidc":
                discovery_url = f"{provider_settings.issuer_url}/.well-known/openid-configuration"
            else:
                discovery_url = registry.discovery_url

            discovery = await fetch_oidc_discovery(discovery_url)
            if discovery is None:
                logger.warning("Skipping provider %s — OIDC discovery failed", name)
                continue

            authorize_url = discovery.get("authorization_endpoint", "")
            token_url = discovery.get("token_endpoint", "")
            userinfo_url = discovery.get("userinfo_endpoint", "")
        else:
            authorize_url = registry.authorize_url
            token_url = registry.token_url
            userinfo_url = registry.userinfo_url

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

    if not resolved:
        raise RuntimeError("No auth providers could be initialized. Check provider configuration and connectivity.")

    logger.info("Initialized auth providers: %s", ", ".join(resolved.keys()))
    return resolved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_auth_service_discovery.py tests/test_auth_service.py -v" 2>&1 | tail -25`
Expected: All tests pass (8 new discovery + 3 existing service tests).

- [ ] **Step 5: Update `backend/app.py` to call `init_providers` on startup**

In `backend/app.py`, add the import at the top:

```python
from backend.auth.service import init_providers
```

After `app.state.settings = settings`, add a startup event:

```python
    @app.on_event("startup")
    async def startup():
        app.state.providers = await init_providers(settings)
```

- [ ] **Step 6: Commit**

```bash
git add backend/auth/service.py backend/app.py tests/test_auth_service_discovery.py
git commit -m "feat: add OIDC discovery and provider initialization on startup"
```

---

### Task 5: Multi-Provider Auth Routes

**Files:**
- Rewrite: `backend/auth/routes.py`
- Rewrite: `tests/test_auth_routes.py`

This task rewrites the auth routes to support multiple providers: `GET /providers`, `GET /login/{provider}`, `GET /callback/{provider}`, `GET /me`, `POST /logout`, `GET /users`, `PATCH /users/{callsign}`.

- [ ] **Step 1: Write tests for new multi-provider routes**

Replace `tests/test_auth_routes.py` entirely:

```python
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import AsyncMock, patch, MagicMock

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.routes import auth_router
from backend.auth.service import create_access_token
from backend.config import Settings, ProviderSettings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
        auth_google=ProviderSettings(enabled=True, client_id="test-gid", client_secret="test-gsec"),
        app_base_url="http://localhost:8000",
    )


@pytest.fixture
def db_setup():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


@pytest.fixture
def test_app(test_settings, db_setup):
    engine, factory = db_setup
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = test_settings
    app.state.providers = {
        "google": {
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
            "client_id": "test-gid",
            "client_secret": "test-gsec",
            "scopes": "openid email profile",
            "label": "Google",
            "protocol": "oidc",
            "extract_subject": lambda d: str(d.get("sub", "")),
            "extract_name": lambda d: d.get("name", "Unknown"),
            "extract_email": lambda d: d.get("email", ""),
        },
    }
    app.include_router(auth_router, prefix="/api/auth")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_providers_returns_enabled(test_client):
    response = await test_client.get("/api/auth/providers")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "google"
    assert data[0]["label"] == "Google"


@pytest.mark.asyncio
async def test_login_unknown_provider_404(test_client):
    response = await test_client.get("/api/auth/login/unknown", follow_redirects=False)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_login_redirects_to_provider(test_client):
    response = await test_client.get("/api/auth/login/google", follow_redirects=False)
    assert response.status_code in (302, 307)
    location = response.headers.get("location", "")
    assert "accounts.google.com" in location


@pytest.mark.asyncio
async def test_callback_creates_pending_user(test_client, test_app, db_setup):
    _, factory = db_setup

    mock_token_response = MagicMock()
    mock_token_response.status_code = 200
    mock_token_response.json.return_value = {"access_token": "fake-token", "token_type": "Bearer"}

    mock_userinfo_response = MagicMock()
    mock_userinfo_response.status_code = 200
    mock_userinfo_response.json.return_value = {
        "sub": "google-123",
        "name": "Test User",
        "email": "test@example.com",
    }

    with patch("backend.auth.routes.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_token_response
        mock_client.get.return_value = mock_userinfo_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # First, do a login to get a state cookie
        login_resp = await test_client.get("/api/auth/login/google", follow_redirects=False)
        cookies = login_resp.cookies

        # Extract state from the redirect URL
        location = login_resp.headers.get("location", "")
        import urllib.parse
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        state = params.get("state", [""])[0]

        response = await test_client.get(
            f"/api/auth/callback/google?code=authcode&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)

    # Verify user was created as PENDING
    with factory() as session:
        user = session.query(User).filter(User.oidc_subject == "google:google-123").first()
        assert user is not None
        assert user.role == UserRole.PENDING
        assert user.name == "Test User"
        assert user.email == "test@example.com"
        assert user.callsign.startswith("PENDING-")


@pytest.mark.asyncio
async def test_callback_existing_user_not_changed(test_client, test_app, db_setup):
    _, factory = db_setup

    # Pre-create user
    with factory() as session:
        user = User(
            callsign="W0NE",
            oidc_subject="google:existing-123",
            name="Existing",
            role=UserRole.ADMIN,
        )
        session.add(user)
        session.commit()

    mock_token_response = MagicMock()
    mock_token_response.status_code = 200
    mock_token_response.json.return_value = {"access_token": "fake-token", "token_type": "Bearer"}

    mock_userinfo_response = MagicMock()
    mock_userinfo_response.status_code = 200
    mock_userinfo_response.json.return_value = {"sub": "existing-123", "name": "Existing", "email": "e@e.com"}

    with patch("backend.auth.routes.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_token_response
        mock_client.get.return_value = mock_userinfo_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        login_resp = await test_client.get("/api/auth/login/google", follow_redirects=False)
        cookies = login_resp.cookies
        location = login_resp.headers.get("location", "")
        import urllib.parse
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        state = params.get("state", [""])[0]

        response = await test_client.get(
            f"/api/auth/callback/google?code=authcode&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)

    with factory() as session:
        user = session.query(User).filter(User.oidc_subject == "google:existing-123").first()
        assert user.role == UserRole.ADMIN  # Not changed to PENDING
        assert user.callsign == "W0NE"


@pytest.mark.asyncio
async def test_me_returns_user(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        user = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
            email="admin@example.com",
            pending_callsign=None,
        )
        session.add(user)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get("/api/auth/me", cookies={"access_token": token})
    assert response.status_code == 200
    data = response.json()
    assert data["callsign"] == "W0NE"
    assert data["name"] == "Admin"
    assert data["role"] == "admin"
    assert data["email"] == "admin@example.com"


@pytest.mark.asyncio
async def test_me_unauthenticated(test_client):
    response = await test_client.get("/api/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_cookie(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/auth/logout",
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token" in set_cookie


@pytest.mark.asyncio
async def test_admin_can_list_users(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get("/api/auth/users", cookies={"access_token": token})
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_admin_can_update_user_role(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.patch(
        "/api/auth/users/KD0TST",
        json={"role": "net_control"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "net_control"


@pytest.mark.asyncio
async def test_viewer_cannot_update_roles(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.patch(
        "/api/auth/users/W0NE",
        json={"role": "viewer"},
        cookies={"access_token": token},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_auth_routes.py -v" 2>&1 | tail -25`
Expected: Failures — old routes don't match new test expectations.

- [ ] **Step 3: Rewrite `backend/auth/routes.py`**

Replace the entire file:

```python
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, get_settings, require_role
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config import Settings

auth_router = APIRouter(tags=["auth"])


def _get_provider_config(request: Request, provider: str) -> dict:
    providers = request.app.state.providers
    if provider not in providers:
        raise HTTPException(status_code=404, detail=f"Unknown auth provider: {provider}")
    return providers[provider]


@auth_router.get("/providers")
async def list_providers(request: Request):
    providers = request.app.state.providers
    return [{"name": name, "label": config["label"]} for name, config in providers.items()]


@auth_router.get("/login/{provider}")
async def login(provider: str, request: Request, app_settings: Settings = Depends(get_settings)):
    config = _get_provider_config(request, provider)

    state = secrets.token_urlsafe(32)
    params = {
        "client_id": config["client_id"],
        "redirect_uri": f"{app_settings.app_base_url}/api/auth/callback/{provider}",
        "response_type": "code",
        "scope": config["scopes"],
        "state": state,
    }
    authorization_url = f"{config['authorize_url']}?{urllib.parse.urlencode(params)}"

    response = RedirectResponse(url=authorization_url)
    response.set_cookie(
        key="oauth_state",
        value=f"{provider}:{state}",
        httponly=True,
        samesite="lax",
        max_age=600,
    )
    return response


@auth_router.get("/callback/{provider}")
async def callback(
    provider: str,
    request: Request,
    code: str,
    state: str = "",
    oauth_state: str | None = Cookie(default=None),
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
):
    # Validate CSRF state
    expected_state = f"{provider}:{state}"
    if not oauth_state or not secrets.compare_digest(oauth_state, expected_state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    config = _get_provider_config(request, provider)
    redirect_uri = f"{app_settings.app_base_url}/api/auth/callback/{provider}"

    async with httpx.AsyncClient() as client:
        # Exchange code for token
        token_response = await client.post(
            config["token_url"],
            data={
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_response.json()
        access_token = token_data.get("access_token", "")

        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to obtain access token from provider")

        # Fetch user info
        userinfo_response = await client.get(
            config["userinfo_url"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo = userinfo_response.json()

    # Extract user details using provider-specific functions
    raw_subject = config["extract_subject"](userinfo)
    if not raw_subject:
        raise HTTPException(status_code=400, detail="Provider did not return a user identifier")

    oidc_subject = f"{provider}:{raw_subject}"
    name = config["extract_name"](userinfo)
    email = config["extract_email"](userinfo)

    # Look up existing user
    user = db.query(User).filter(User.oidc_subject == oidc_subject).first()

    if user is None:
        # Check if first user (auto-admin)
        user_count = db.query(User).count()
        role = UserRole.ADMIN if user_count == 0 else UserRole.PENDING

        placeholder_callsign = f"PENDING-{oidc_subject[:12]}"

        user = User(
            callsign=placeholder_callsign,
            oidc_subject=oidc_subject,
            name=name,
            role=role,
            email=email or None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    jwt_token = create_access_token(user.callsign, user.role.value, app_settings)
    response = RedirectResponse(url=app_settings.app_base_url)
    is_secure = app_settings.app_base_url.startswith("https://")
    response.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=app_settings.jwt_expire_minutes * 60,
    )
    return response


@auth_router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {
        "callsign": user.callsign,
        "name": user.name,
        "role": user.role.value,
        "email": user.email,
        "pending_callsign": user.pending_callsign,
    }


@auth_router.post("/logout")
async def logout():
    response = Response(content='{"message": "logged out"}', media_type="application/json")
    response.delete_cookie(key="access_token", httponly=True, samesite="lax")
    return response


class UserRoleUpdate(BaseModel):
    role: UserRole


@auth_router.get("/users")
async def list_users(
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    users = db.query(User).order_by(User.callsign).all()
    return [
        {
            "callsign": u.callsign,
            "name": u.name,
            "role": u.role.value,
            "email": u.email,
            "pending_callsign": u.pending_callsign,
        }
        for u in users
    ]


@auth_router.patch("/users/{callsign}")
async def update_user_role(
    callsign: str,
    body: UserRoleUpdate,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    target_user = db.get(User, callsign)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    target_user.role = body.role
    db.commit()
    db.refresh(target_user)
    return {
        "callsign": target_user.callsign,
        "name": target_user.name,
        "role": target_user.role.value,
        "email": target_user.email,
        "pending_callsign": target_user.pending_callsign,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_auth_routes.py -v" 2>&1 | tail -25`
Expected: All 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/auth/routes.py tests/test_auth_routes.py
git commit -m "feat: rewrite auth routes for multi-provider support"
```

---

### Task 6: Registration + Callsign Change Routes

**Files:**
- Modify: `backend/auth/routes.py`
- Create: `tests/test_auth_registration.py`

- [ ] **Step 1: Write tests for registration and callsign change**

Create `tests/test_auth_registration.py`:

```python
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.routes import auth_router
from backend.auth.service import create_access_token
from backend.config import Settings, ProviderSettings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
        app_base_url="http://localhost:8000",
    )


@pytest.fixture
def db_setup():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


@pytest.fixture
def test_app(test_settings, db_setup):
    engine, factory = db_setup
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = test_settings
    app.state.providers = {}
    app.include_router(auth_router, prefix="/api/auth")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Registration tests ---


@pytest.mark.asyncio
async def test_register_valid_callsign(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(
            callsign="PENDING-google12",
            oidc_subject="google:123",
            name="New User",
            role=UserRole.PENDING,
        ))
        session.commit()

    token = create_access_token("PENDING-google12", "pending", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "W0ABC"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["callsign"] == "W0ABC"
    assert data["role"] == "pending"  # Still pending until admin approves


@pytest.mark.asyncio
async def test_register_invalid_callsign_format(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(
            callsign="PENDING-google12",
            oidc_subject="google:123",
            name="New User",
            role=UserRole.PENDING,
        ))
        session.commit()

    token = create_access_token("PENDING-google12", "pending", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "not-a-callsign"},
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_register_duplicate_callsign(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(
            callsign="W0NE",
            oidc_subject="google:existing",
            name="Existing",
            role=UserRole.ADMIN,
        ))
        session.add(User(
            callsign="PENDING-google12",
            oidc_subject="google:new",
            name="New User",
            role=UserRole.PENDING,
        ))
        session.commit()

    token = create_access_token("PENDING-google12", "pending", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "W0NE"},
        cookies={"access_token": token},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_already_registered(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(
            callsign="W0NE",
            oidc_subject="google:existing",
            name="Existing",
            role=UserRole.VIEWER,
        ))
        session.commit()

    token = create_access_token("W0NE", "viewer", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "W0NEW"},
        cookies={"access_token": token},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_unauthenticated(test_client):
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "W0ABC"},
    )
    assert response.status_code == 401


# --- Callsign change request tests ---


@pytest.mark.asyncio
async def test_request_callsign_change(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(
            callsign="W0OLD",
            oidc_subject="google:user1",
            name="User One",
            role=UserRole.VIEWER,
        ))
        session.commit()

    token = create_access_token("W0OLD", "viewer", test_settings)
    response = await test_client.patch(
        "/api/auth/me",
        json={"callsign": "W0NEW"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["pending_callsign"] == "W0NEW"

    # Verify the actual callsign hasn't changed yet
    with factory() as session:
        user = session.get(User, "W0OLD")
        assert user is not None
        assert user.pending_callsign == "W0NEW"


@pytest.mark.asyncio
async def test_request_callsign_change_invalid_format(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(
            callsign="W0OLD",
            oidc_subject="google:user1",
            name="User One",
            role=UserRole.VIEWER,
        ))
        session.commit()

    token = create_access_token("W0OLD", "viewer", test_settings)
    response = await test_client.patch(
        "/api/auth/me",
        json={"callsign": "invalid"},
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_request_callsign_change_taken(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0OLD", oidc_subject="google:u1", name="User", role=UserRole.VIEWER))
        session.add(User(callsign="W0NEW", oidc_subject="google:u2", name="Other", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("W0OLD", "viewer", test_settings)
    response = await test_client.patch(
        "/api/auth/me",
        json={"callsign": "W0NEW"},
        cookies={"access_token": token},
    )
    assert response.status_code == 409


# --- Callsign approval tests ---


@pytest.mark.asyncio
async def test_approve_callsign_change(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(
            callsign="W0OLD",
            oidc_subject="google:user1",
            name="User",
            role=UserRole.VIEWER,
            pending_callsign="W0NEW",
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/auth/users/W0OLD/approve-callsign",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["callsign"] == "W0NEW"
    assert data["pending_callsign"] is None


@pytest.mark.asyncio
async def test_approve_callsign_no_pending(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(callsign="W0OLD", oidc_subject="google:u1", name="User", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/auth/users/W0OLD/approve-callsign",
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reject_callsign_change(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(
            callsign="W0OLD",
            oidc_subject="google:u1",
            name="User",
            role=UserRole.VIEWER,
            pending_callsign="W0NEW",
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.delete(
        "/api/auth/users/W0OLD/pending-callsign",
        cookies={"access_token": token},
    )
    assert response.status_code == 200

    with factory() as session:
        user = session.get(User, "W0OLD")
        assert user.pending_callsign is None


@pytest.mark.asyncio
async def test_viewer_cannot_approve_callsign(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(
            callsign="KD0TST",
            oidc_subject="google:viewer",
            name="Viewer",
            role=UserRole.VIEWER,
            pending_callsign="W0NEW",
        ))
        session.commit()

    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.post(
        "/api/auth/users/KD0TST/approve-callsign",
        cookies={"access_token": token},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_auth_registration.py -v" 2>&1 | tail -25`
Expected: Failures — registration and callsign change endpoints don't exist yet.

- [ ] **Step 3: Add registration and callsign change routes to `backend/auth/routes.py`**

Add these imports at the top (after existing imports):

```python
import re
```

Add the `CALLSIGN_PATTERN` constant after the `auth_router` line:

```python
CALLSIGN_PATTERN = re.compile(r"^[A-Z]{1,2}\d[A-Z]{1,4}$")
```

Add these route functions before the `UserRoleUpdate` class:

```python
class RegisterRequest(BaseModel):
    callsign: str


@auth_router.post("/register")
async def register(
    body: RegisterRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    if user.role != UserRole.PENDING:
        raise HTTPException(status_code=409, detail="User already registered")

    callsign = body.callsign.upper()
    if not CALLSIGN_PATTERN.match(callsign):
        raise HTTPException(status_code=400, detail="Invalid callsign format")

    existing = db.get(User, callsign)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Callsign already taken")

    # Update primary key — SQLite handles this as a direct UPDATE
    old_callsign = user.callsign
    db.execute(
        sa.text("UPDATE users SET callsign = :new WHERE callsign = :old"),
        {"new": callsign, "old": old_callsign},
    )
    db.commit()

    user = db.get(User, callsign)
    return {
        "callsign": user.callsign,
        "name": user.name,
        "role": user.role.value,
        "email": user.email,
        "pending_callsign": user.pending_callsign,
    }


class CallsignChangeRequest(BaseModel):
    callsign: str


@auth_router.patch("/me")
async def update_me(
    body: CallsignChangeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    callsign = body.callsign.upper()
    if not CALLSIGN_PATTERN.match(callsign):
        raise HTTPException(status_code=400, detail="Invalid callsign format")

    existing = db.get(User, callsign)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Callsign already taken")

    user.pending_callsign = callsign
    db.commit()
    db.refresh(user)

    return {
        "callsign": user.callsign,
        "name": user.name,
        "role": user.role.value,
        "email": user.email,
        "pending_callsign": user.pending_callsign,
    }
```

Add the `sa` import at the top:

```python
import sqlalchemy as sa
```

Add callsign approval/rejection routes after the `update_user_role` function:

```python
@auth_router.post("/users/{callsign}/approve-callsign")
async def approve_callsign(
    callsign: str,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    target_user = db.get(User, callsign)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not target_user.pending_callsign:
        raise HTTPException(status_code=400, detail="No pending callsign change")

    new_callsign = target_user.pending_callsign

    # Check new callsign isn't taken
    existing = db.get(User, new_callsign)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Callsign already taken")

    # Update primary key
    db.execute(
        sa.text("UPDATE users SET callsign = :new, pending_callsign = NULL WHERE callsign = :old"),
        {"new": new_callsign, "old": callsign},
    )
    db.commit()

    updated_user = db.get(User, new_callsign)
    return {
        "callsign": updated_user.callsign,
        "name": updated_user.name,
        "role": updated_user.role.value,
        "email": updated_user.email,
        "pending_callsign": updated_user.pending_callsign,
    }


@auth_router.delete("/users/{callsign}/pending-callsign")
async def reject_callsign(
    callsign: str,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    target_user = db.get(User, callsign)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.pending_callsign = None
    db.commit()
    return {"message": "Pending callsign change rejected"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_auth_registration.py -v" 2>&1 | tail -25`
Expected: All 12 tests pass.

- [ ] **Step 5: Run all auth tests**

Run: `nix-shell --run "python -m pytest tests/test_auth_*.py -v" 2>&1 | tail -30`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/auth/routes.py tests/test_auth_registration.py
git commit -m "feat: add registration and callsign change endpoints with approval flow"
```

---

### Task 7: Email Module

**Files:**
- Create: `backend/auth/email.py`
- Create: `tests/test_auth_email.py`

- [ ] **Step 1: Write tests for email module**

Create `tests/test_auth_email.py`:

```python
import pytest
from unittest.mock import patch, MagicMock

from backend.auth.email import send_email, notify_admins_new_registration, notify_admins_callsign_change, notify_user_approved, notify_user_callsign_approved
from backend.auth.models import User, UserRole
from backend.config import Settings, ProviderSettings, SmtpSettings


@pytest.fixture
def smtp_settings():
    return Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
        app_base_url="http://localhost:8000",
        smtp=SmtpSettings(
            host="smtp.example.com",
            port=587,
            username="test@example.com",
            password="password",
            use_tls=True,
            from_address="skynet@example.com",
        ),
    )


@pytest.fixture
def no_smtp_settings():
    return Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
    )


@pytest.mark.asyncio
async def test_send_email_success(smtp_settings):
    with patch("backend.auth.email.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        await send_email("recipient@example.com", "Test Subject", "Test body", smtp_settings)

        mock_smtp.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_no_smtp_configured(no_smtp_settings):
    # Should not raise — just silently skip
    await send_email("recipient@example.com", "Test Subject", "Test body", no_smtp_settings)


@pytest.mark.asyncio
async def test_send_email_failure_does_not_raise(smtp_settings):
    with patch("backend.auth.email.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.side_effect = Exception("Connection refused")

        # Should not raise
        await send_email("recipient@example.com", "Test Subject", "Test body", smtp_settings)


@pytest.mark.asyncio
async def test_notify_admins_new_registration(smtp_settings):
    admin = User(callsign="W0NE", oidc_subject="g:1", name="Admin", role=UserRole.ADMIN, email="admin@example.com")
    new_user = User(callsign="W0ABC", oidc_subject="g:2", name="New User", role=UserRole.PENDING)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_admins_new_registration([admin], new_user, smtp_settings)
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "W0ABC" in call_args[0][1]  # subject contains callsign
        assert "admin@example.com" == call_args[0][0]


@pytest.mark.asyncio
async def test_notify_admins_skips_admins_without_email(smtp_settings):
    admin_no_email = User(callsign="W0NE", oidc_subject="g:1", name="Admin", role=UserRole.ADMIN, email=None)
    new_user = User(callsign="W0ABC", oidc_subject="g:2", name="New User", role=UserRole.PENDING)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_admins_new_registration([admin_no_email], new_user, smtp_settings)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_notify_user_approved(smtp_settings):
    user = User(callsign="W0ABC", oidc_subject="g:1", name="User", role=UserRole.VIEWER, email="user@example.com")

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_user_approved(user, smtp_settings)
        mock_send.assert_called_once()
        assert "approved" in mock_send.call_args[0][1].lower()


@pytest.mark.asyncio
async def test_notify_user_approved_no_email(smtp_settings):
    user = User(callsign="W0ABC", oidc_subject="g:1", name="User", role=UserRole.VIEWER, email=None)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_user_approved(user, smtp_settings)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_notify_admins_callsign_change(smtp_settings):
    admin = User(callsign="W0NE", oidc_subject="g:1", name="Admin", role=UserRole.ADMIN, email="admin@example.com")
    user = User(callsign="W0OLD", oidc_subject="g:2", name="User", role=UserRole.VIEWER)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_admins_callsign_change([admin], user, "W0NEW", smtp_settings)
        mock_send.assert_called_once()
        subject = mock_send.call_args[0][1]
        assert "W0OLD" in subject
        assert "W0NEW" in subject


@pytest.mark.asyncio
async def test_notify_user_callsign_approved(smtp_settings):
    user = User(callsign="W0NEW", oidc_subject="g:1", name="User", role=UserRole.VIEWER, email="user@example.com")

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_user_callsign_approved(user, "W0OLD", smtp_settings)
        mock_send.assert_called_once()
        body = mock_send.call_args[0][2]
        assert "W0NEW" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_auth_email.py -v" 2>&1 | tail -20`
Expected: ImportError — `backend.auth.email` does not exist.

- [ ] **Step 3: Create `backend/auth/email.py`**

```python
import asyncio
import logging
import smtplib
from email.message import EmailMessage

from backend.auth.models import User
from backend.config import Settings

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, body: str, settings: Settings) -> None:
    """Send an email via SMTP. Fire-and-forget — never raises."""
    if not settings.smtp.host:
        logger.debug("SMTP not configured, skipping email to %s", to)
        return

    try:
        await asyncio.to_thread(_send_email_sync, to, subject, body, settings)
    except Exception:
        logger.exception("Failed to send email to %s", to)


def _send_email_sync(to: str, subject: str, body: str, settings: Settings) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp.from_address
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp.host, settings.smtp.port) as server:
        if settings.smtp.use_tls:
            server.starttls()
        if settings.smtp.username:
            server.login(settings.smtp.username, settings.smtp.password)
        server.send_message(msg)


async def notify_admins_new_registration(admins: list[User], new_user: User, settings: Settings) -> None:
    """Notify admins that a new user has registered."""
    for admin in admins:
        if admin.email:
            await send_email(
                admin.email,
                f"[SkyNetControl] New registration: {new_user.callsign}",
                f"{new_user.name} has registered as {new_user.callsign} and is awaiting approval. "
                f"Review pending users at {settings.app_base_url}.",
                settings,
            )


async def notify_admins_callsign_change(
    admins: list[User], user: User, new_callsign: str, settings: Settings
) -> None:
    """Notify admins that a user has requested a callsign change."""
    for admin in admins:
        if admin.email:
            await send_email(
                admin.email,
                f"[SkyNetControl] Callsign change request: {user.callsign} → {new_callsign}",
                f"{user.name} ({user.callsign}) has requested a callsign change to {new_callsign}. "
                f"Review at {settings.app_base_url}.",
                settings,
            )


async def notify_user_approved(user: User, settings: Settings) -> None:
    """Notify a user that their account has been approved."""
    if not user.email:
        return
    await send_email(
        user.email,
        "[SkyNetControl] Your account has been approved",
        f"Your account ({user.callsign}) has been approved as {user.role.value}. "
        f"You can now access SkyNetControl at {settings.app_base_url}.",
        settings,
    )


async def notify_user_callsign_approved(user: User, old_callsign: str, settings: Settings) -> None:
    """Notify a user that their callsign change has been approved."""
    if not user.email:
        return
    await send_email(
        user.email,
        "[SkyNetControl] Your callsign change has been approved",
        f"Your callsign has been changed from {old_callsign} to {user.callsign}. "
        f"Access SkyNetControl at {settings.app_base_url}.",
        settings,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_auth_email.py -v" 2>&1 | tail -20`
Expected: All 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/auth/email.py tests/test_auth_email.py
git commit -m "feat: add SMTP email notification module"
```

---

### Task 8: Wire Email Notifications Into Routes

**Files:**
- Modify: `backend/auth/routes.py`

This task integrates the email module into the registration, callsign change, and approval routes. No new test file — the email calls are fire-and-forget and tested via the email module tests. The route tests from Tasks 5 and 6 already verify the core logic.

- [ ] **Step 1: Add email imports to `backend/auth/routes.py`**

Add at the top with the other imports:

```python
from backend.auth.email import (
    notify_admins_new_registration,
    notify_admins_callsign_change,
    notify_user_approved,
    notify_user_callsign_approved,
)
```

- [ ] **Step 2: Add email notification to `register` endpoint**

In the `register` function, after `user = db.get(User, callsign)` and before the return statement, add:

```python
    # Notify admins (fire-and-forget)
    admins = db.query(User).filter(User.role == UserRole.ADMIN).all()
    await notify_admins_new_registration(admins, user, app_settings)
```

Also add `app_settings: Settings = Depends(get_settings)` to the function signature.

- [ ] **Step 3: Add email notification to `update_me` (callsign change request)**

In the `update_me` function, after `db.refresh(user)` and before the return statement, add:

```python
    # Notify admins (fire-and-forget)
    admins = db.query(User).filter(User.role == UserRole.ADMIN).all()
    await notify_admins_callsign_change(admins, user, callsign, app_settings)
```

Also add `app_settings: Settings = Depends(get_settings)` to the function signature.

- [ ] **Step 4: Add email notification to `update_user_role` (approval)**

In the `update_user_role` function, after `db.refresh(target_user)` and before the return statement, add:

```python
    # Notify user if they were just approved (role changed from PENDING)
    if body.role != UserRole.PENDING:
        old_role_was_pending = target_user.role == UserRole.PENDING  # Already committed, check body
        # We need to detect if the role change was from PENDING
        # Since we already committed, just notify if the new role is not PENDING
        await notify_user_approved(target_user, app_settings)
```

Wait — we need to capture the old role before the commit. Restructure the function:

In `update_user_role`, before `target_user.role = body.role`, add:

```python
    was_pending = target_user.role == UserRole.PENDING
```

After `db.refresh(target_user)`, add:

```python
    if was_pending and target_user.role != UserRole.PENDING:
        await notify_user_approved(target_user, app_settings)
```

Also add `app_settings: Settings = Depends(get_settings)` to the function signature.

- [ ] **Step 5: Add email notification to `approve_callsign`**

In the `approve_callsign` function, after `updated_user = db.get(User, new_callsign)` and before the return statement, add:

```python
    await notify_user_callsign_approved(updated_user, callsign, app_settings)
```

Also add `app_settings: Settings = Depends(get_settings)` to the function signature.

- [ ] **Step 6: Run all tests**

Run: `nix-shell --run "python -m pytest tests/ -v" 2>&1 | tail -30`
Expected: All tests pass. Email functions are mocked in email tests; route tests don't mock email (the SMTP host is empty in test_settings so email is silently skipped).

- [ ] **Step 7: Commit**

```bash
git add backend/auth/routes.py
git commit -m "feat: wire email notifications into auth routes"
```

---

### Task 9: Fix Remaining Test Compatibility

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_auth_dependencies.py`

The config change in Task 1 removed the old `oidc_*` fields from `Settings`. Tests that reference those fields need updating.

- [ ] **Step 1: Update `tests/conftest.py`**

The existing `test_settings` fixture doesn't reference OIDC settings, so no change needed. But verify:

Run: `nix-shell --run "python -m pytest tests/conftest.py --co" 2>&1 | tail -10`
Expected: Collection succeeds.

- [ ] **Step 2: Check for any remaining references to old OIDC settings**

Run: `grep -r "oidc_issuer_url\|oidc_client_id\|oidc_client_secret\|oidc_redirect_uri" tests/`

If any test files still reference these old settings, update them to use the new `auth_google` / `auth_oidc` pattern.

- [ ] **Step 3: Run the full test suite**

Run: `nix-shell --run "python -m pytest tests/ -v" 2>&1 | tail -40`
Expected: All tests pass.

- [ ] **Step 4: Run ruff checks**

Run: `nix-shell --run "ruff check backend/ tests/"`
Run: `nix-shell --run "ruff format --check backend/ tests/"`
Expected: Clean on both.

- [ ] **Step 5: Fix any ruff findings**

If ruff reports issues, fix them. Run: `nix-shell --run "ruff format backend/ tests/"` for formatting issues.

- [ ] **Step 6: Commit**

```bash
git add tests/ backend/
git commit -m "fix: update tests for new auth provider config structure"
```

---

### Task 10: Secrets Management Documentation

**Files:**
- Create: `docs/deployment/secrets.md`

- [ ] **Step 1: Create directory**

```bash
mkdir -p docs/deployment
```

- [ ] **Step 2: Create `docs/deployment/secrets.md`**

```markdown
# Secrets Management

SkyNetControl uses environment variables for all secrets. Pydantic Settings loads them with the `SKYNET_` prefix.

## Required Secrets

| Variable | Purpose | How to Generate |
|----------|---------|----------------|
| `SKYNET_JWT_SECRET_KEY` | JWT signing key | `openssl rand -hex 32` |
| `SKYNET_DATABASE_URL` | Database connection string | Provider-specific |

## Auth Provider Credentials

Each enabled provider needs a client ID and secret from the provider's developer console:

| Provider | Client ID | Client Secret | Extra |
|----------|-----------|--------------|-------|
| Google | `SKYNET_AUTH_GOOGLE_CLIENT_ID` | `SKYNET_AUTH_GOOGLE_CLIENT_SECRET` | [Google Cloud Console](https://console.cloud.google.com/apis/credentials) |
| Microsoft | `SKYNET_AUTH_MICROSOFT_CLIENT_ID` | `SKYNET_AUTH_MICROSOFT_CLIENT_SECRET` | [Azure Portal](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps) |
| GitHub | `SKYNET_AUTH_GITHUB_CLIENT_ID` | `SKYNET_AUTH_GITHUB_CLIENT_SECRET` | [GitHub Developer Settings](https://github.com/settings/developers) |
| Discord | `SKYNET_AUTH_DISCORD_CLIENT_ID` | `SKYNET_AUTH_DISCORD_CLIENT_SECRET` | [Discord Developer Portal](https://discord.com/developers/applications) |
| Facebook | `SKYNET_AUTH_FACEBOOK_CLIENT_ID` | `SKYNET_AUTH_FACEBOOK_CLIENT_SECRET` | [Meta Developer Dashboard](https://developers.facebook.com/apps/) |
| Generic OIDC | `SKYNET_AUTH_OIDC_CLIENT_ID` | `SKYNET_AUTH_OIDC_CLIENT_SECRET` | Also set `SKYNET_AUTH_OIDC_ISSUER_URL` |

Enable a provider by setting `SKYNET_AUTH_{PROVIDER}_ENABLED=true`.

## SMTP Credentials (Optional)

| Variable | Purpose | Default |
|----------|---------|---------|
| `SKYNET_SMTP_HOST` | SMTP server | (empty — email disabled) |
| `SKYNET_SMTP_PORT` | SMTP port | `587` |
| `SKYNET_SMTP_USERNAME` | SMTP login | (empty) |
| `SKYNET_SMTP_PASSWORD` | SMTP password | (empty) |
| `SKYNET_SMTP_USE_TLS` | Use STARTTLS | `true` |
| `SKYNET_SMTP_FROM_ADDRESS` | From header | (empty) |

If `SKYNET_SMTP_HOST` is not set, email notifications are silently disabled.

## Deployment Patterns

### NixOS with sops-nix

```nix
sops.secrets."skynetcontrol/env" = {};

systemd.services.skynetcontrol.serviceConfig.EnvironmentFile =
  config.sops.secrets."skynetcontrol/env".path;
```

### NixOS with agenix

```nix
age.secrets.skynetcontrol-env.file = ../secrets/skynetcontrol-env.age;

systemd.services.skynetcontrol.serviceConfig.EnvironmentFile =
  config.age.secrets.skynetcontrol-env.path;
```

### systemd EnvironmentFile

```ini
# /etc/skynetcontrol/env (mode 0600, owned by service user)
SKYNET_JWT_SECRET_KEY=hex-string-here
SKYNET_AUTH_GOOGLE_ENABLED=true
SKYNET_AUTH_GOOGLE_CLIENT_ID=your-client-id
SKYNET_AUTH_GOOGLE_CLIENT_SECRET=your-client-secret
SKYNET_SMTP_HOST=smtp.example.com
SKYNET_SMTP_PASSWORD=app-password-here
```

### Docker / OCI

```bash
docker run --env-file /path/to/env ghcr.io/owner/skynetcontrol:latest
```

## Do NOT

- Commit secrets to git
- Use the default `jwt_secret_key` value (`change-me-in-production`) in production
- Pass secrets as command-line arguments (visible in `ps`)
```

- [ ] **Step 3: Commit**

```bash
git add docs/deployment/secrets.md
git commit -m "docs: add secrets management deployment guide"
```

---

### Task 11: Verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `nix-shell --run "python -m pytest tests/ -v" 2>&1 | tail -40`
Expected: All tests pass.

- [ ] **Step 2: Run ruff**

Run: `nix-shell --run "ruff check backend/ tests/"`
Run: `nix-shell --run "ruff format --check backend/ tests/"`
Expected: Clean on both.

- [ ] **Step 3: Verify nix-build**

Run: `nix-build default.nix`
Expected: Successful build.

- [ ] **Step 4: List all new/modified files**

Run: `git log --oneline --name-status main..HEAD`
Expected files:
- `A  backend/auth/providers.py`
- `A  backend/auth/email.py`
- `M  backend/auth/models.py`
- `M  backend/auth/dependencies.py`
- `M  backend/auth/service.py`
- `M  backend/auth/routes.py`
- `M  backend/config.py`
- `M  backend/app.py`
- `A  alembic/versions/XXXX_add_pending_role_email_callsign.py`
- `A  docs/deployment/secrets.md`
- `A  tests/test_auth_providers.py`
- `A  tests/test_auth_email.py`
- `A  tests/test_auth_registration.py`
- `A  tests/test_auth_service_discovery.py`
- `M  tests/test_auth_models.py`
- `M  tests/test_auth_dependencies.py`
- `M  tests/test_auth_routes.py`
