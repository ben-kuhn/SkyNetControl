# Phase 1: Auth + Config + Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OIDC authentication, application configuration, and net schedule management so users can log in, configure the app, and manage net seasons and sessions.

**Architecture:** OIDC auth code flow via authlib, JWT tokens in HTTP-only cookies, role-based access control (admin/net_control/viewer). App configuration stored in DB. Schedule module with season-based auto-generation of sessions. All new API routes under `/api/`.

**Tech Stack:** authlib (OIDC), python-jose (JWT), SQLAlchemy models, Alembic migrations, FastAPI dependency injection for auth

---

## File Structure

```
backend/
├── app.py                          # Modified: register routers, add OIDC middleware
├── config.py                       # Modified: add OIDC + JWT settings
├── db/
│   ├── base.py                     # Existing (unchanged)
│   └── session.py                  # Existing (unchanged)
├── auth/
│   ├── __init__.py
│   ├── models.py                   # User model (callsign PK, oidc_subject, role, name)
│   ├── dependencies.py             # get_current_user, require_role dependencies
│   ├── service.py                  # OIDC flow logic, JWT creation/validation
│   └── routes.py                   # /api/auth/* endpoints
├── config_mgmt/
│   ├── __init__.py
│   ├── models.py                   # AppConfig model (key-value)
│   ├── service.py                  # Config get/set logic
│   └── routes.py                   # /api/config/* endpoints
└── modules/
    └── schedule/
        ├── __init__.py
        ├── models.py               # NetSeason, NetSession models
        ├── service.py              # Session generation, CRUD logic
        └── routes.py               # /api/schedule/* endpoints
alembic/
└── versions/
    └── 001_add_users_and_config.py # First migration
    └── 002_add_schedule.py         # Schedule tables
tests/
├── conftest.py                     # Modified: add DB table creation, auth fixtures
├── test_auth.py                    # Auth endpoint tests
├── test_config.py                  # Config API tests
├── test_schedule_models.py         # Schedule model/service tests
└── test_schedule_api.py            # Schedule API endpoint tests
# Note: Frontend UI for auth/config/schedule will be added in a separate phase.
# This plan covers backend API only.
```

---

### Task 1: Add Auth Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `shell.nix`

- [ ] **Step 1: Add Python auth dependencies to pyproject.toml**

Add to the `dependencies` list in `pyproject.toml`:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "sqlalchemy>=2.0.0",
    "alembic>=1.14.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "authlib>=1.3.0",
    "python-jose[cryptography]>=3.3.0",
    "httpx>=0.28.0",
]
```

Note: `httpx` moved from dev to main dependencies — authlib needs it for OIDC token exchange.

Update dev dependencies to avoid duplication:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]
```

- [ ] **Step 2: Install updated dependencies**

Run: `nix-shell --run "pip install -e '.[dev]' --quiet && python -c 'import authlib; print(authlib.__version__)'"`

Expected: authlib version printed

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add auth dependencies (authlib, python-jose)"
```

---

### Task 2: User Model

**Files:**
- Create: `backend/auth/__init__.py`
- Create: `backend/auth/models.py`
- Create: `tests/test_auth_models.py`

- [ ] **Step 1: Create auth package**

`backend/auth/__init__.py`:
```python
```

- [ ] **Step 2: Write the failing test**

`tests/test_auth_models.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.auth.models import User, UserRole


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        yield session
    engine.dispose()


def test_create_user(db: Session):
    user = User(
        callsign="W0NE",
        oidc_subject="auth0|12345",
        name="John Doe",
        role=UserRole.ADMIN,
    )
    db.add(user)
    db.commit()

    fetched = db.get(User, "W0NE")
    assert fetched is not None
    assert fetched.callsign == "W0NE"
    assert fetched.oidc_subject == "auth0|12345"
    assert fetched.name == "John Doe"
    assert fetched.role == UserRole.ADMIN


def test_callsign_is_primary_key(db: Session):
    user = User(
        callsign="KD0TEST",
        oidc_subject="auth0|99999",
        name="Test User",
        role=UserRole.VIEWER,
    )
    db.add(user)
    db.commit()

    fetched = db.get(User, "KD0TEST")
    assert fetched is not None


def test_user_role_defaults_to_viewer(db: Session):
    user = User(
        callsign="N0CALL",
        oidc_subject="auth0|11111",
        name="New User",
    )
    db.add(user)
    db.commit()

    fetched = db.get(User, "N0CALL")
    assert fetched is not None
    assert fetched.role == UserRole.VIEWER


def test_oidc_subject_is_unique(db: Session):
    user1 = User(callsign="W0AAA", oidc_subject="auth0|same", name="User 1")
    user2 = User(callsign="W0BBB", oidc_subject="auth0|same", name="User 2")
    db.add(user1)
    db.commit()
    db.add(user2)
    with pytest.raises(Exception):
        db.commit()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_auth_models.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 4: Implement User model**

`backend/auth/models.py`:

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


class User(Base):
    __tablename__ = "users"

    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    oidc_subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), nullable=False, default=UserRole.VIEWER
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_auth_models.py -v"`

Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add backend/auth/__init__.py backend/auth/models.py tests/test_auth_models.py
git commit -m "feat: add User model with callsign as primary key"
```

---

### Task 3: Auth Config Settings

**Files:**
- Modify: `backend/config.py`

- [ ] **Step 1: Update Settings with OIDC and JWT fields**

Replace `backend/config.py`:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///skynetcontrol.db"
    static_dir: str = "frontend/dist"
    debug: bool = False

    # OIDC
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # App
    app_base_url: str = "http://localhost:8000"

    model_config = {"env_prefix": "SKYNET_"}


settings = Settings()
```

- [ ] **Step 2: Verify existing tests still pass**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All 7 existing tests pass (Settings defaults are backward-compatible)

- [ ] **Step 3: Commit**

```bash
git add backend/config.py
git commit -m "feat: add OIDC and JWT settings to config"
```

---

### Task 4: JWT and Auth Service

**Files:**
- Create: `backend/auth/service.py`
- Create: `tests/test_auth_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_auth_service.py`:

```python
import pytest
from datetime import datetime, timezone

from backend.auth.service import create_access_token, decode_access_token
from backend.config import Settings


@pytest.fixture
def auth_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_algorithm="HS256",
        jwt_expire_minutes=60,
    )


def test_create_and_decode_token(auth_settings):
    token = create_access_token(
        callsign="W0NE",
        role="admin",
        settings=auth_settings,
    )
    assert isinstance(token, str)

    payload = decode_access_token(token, settings=auth_settings)
    assert payload is not None
    assert payload["sub"] == "W0NE"
    assert payload["role"] == "admin"


def test_decode_invalid_token(auth_settings):
    payload = decode_access_token("invalid.token.here", settings=auth_settings)
    assert payload is None


def test_decode_wrong_secret(auth_settings):
    token = create_access_token(
        callsign="W0NE",
        role="admin",
        settings=auth_settings,
    )
    wrong_settings = Settings(
        database_url="sqlite:///",
        jwt_secret_key="wrong-secret",
    )
    payload = decode_access_token(token, settings=wrong_settings)
    assert payload is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_auth_service.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement auth service**

`backend/auth/service.py`:

```python
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from backend.config import Settings


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
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_auth_service.py -v"`

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/auth/service.py tests/test_auth_service.py
git commit -m "feat: add JWT token creation and validation"
```

---

### Task 5: Auth Dependencies (get_current_user, require_role)

**Files:**
- Create: `backend/auth/dependencies.py`
- Create: `tests/test_auth_dependencies.py`

- [ ] **Step 1: Write the failing test**

`tests/test_auth_dependencies.py`:

```python
import pytest
from fastapi import FastAPI, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.auth.dependencies import get_db_session, get_current_user, require_role
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def db_engine():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session_factory(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def seeded_db(db_session_factory):
    with db_session_factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer User",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, viewer])
        session.commit()
    return db_session_factory


@pytest.fixture
def test_app(test_settings, seeded_db):
    app = FastAPI()
    app.state.session_factory = seeded_db
    app.state.settings = test_settings

    @app.get("/api/test/me")
    async def me(user: User = Depends(get_current_user)):
        return {"callsign": user.callsign, "role": user.role.value}

    @app.get("/api/test/admin-only")
    async def admin_only(user: User = Depends(require_role(UserRole.ADMIN))):
        return {"message": "admin access granted"}

    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_authenticated_user(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/test/me", cookies={"access_token": token}
    )
    assert response.status_code == 200
    assert response.json()["callsign"] == "W0NE"


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(test_client):
    response = await test_client.get("/api/test/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_role_required(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/test/admin-only", cookies={"access_token": token}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_access_admin(test_client, test_settings):
    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.get(
        "/api/test/admin-only", cookies={"access_token": token}
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_auth_dependencies.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement auth dependencies**

`backend/auth/dependencies.py`:

```python
from typing import Callable

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.models import User, UserRole
from backend.auth.service import decode_access_token
from backend.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db_session(request: Request):
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
) -> User:
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(access_token, settings=app_settings)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    callsign = payload.get("sub")
    if not callsign:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = db.get(User, callsign)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def require_role(*roles: UserRole) -> Callable:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dependency
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_auth_dependencies.py -v"`

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/auth/dependencies.py tests/test_auth_dependencies.py
git commit -m "feat: add auth dependencies (get_current_user, require_role)"
```

---

### Task 6: Auth Routes (Login, Callback, Logout, Me)

**Files:**
- Create: `backend/auth/routes.py`
- Create: `tests/test_auth_routes.py`

- [ ] **Step 1: Write the failing test**

`tests/test_auth_routes.py`:

```python
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import AsyncMock, patch

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.routes import auth_router
from backend.auth.service import create_access_token
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
        oidc_issuer_url="https://idp.example.com",
        oidc_client_id="test-client",
        oidc_client_secret="test-secret",
        oidc_redirect_uri="http://localhost:8000/api/auth/callback",
        app_base_url="http://localhost:8000",
    )


@pytest.fixture
def db_setup():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


@pytest.fixture
def test_app(test_settings, db_setup):
    engine, factory = db_setup
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = test_settings
    app.include_router(auth_router, prefix="/api/auth")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_me_returns_user(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        user = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        session.add(user)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/auth/me", cookies={"access_token": token}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["callsign"] == "W0NE"
    assert data["name"] == "Admin"
    assert data["role"] == "admin"


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
    # Check that the cookie is being cleared (max_age=0 or expires in past)
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token" in set_cookie


@pytest.mark.asyncio
async def test_login_redirects(test_client):
    with patch("backend.auth.routes._get_oidc_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.create_authorization_url.return_value = (
            "https://idp.example.com/authorize?client_id=test",
            "random-state",
        )
        mock_get_client.return_value = mock_client

        response = await test_client.get(
            "/api/auth/login", follow_redirects=False
        )
        assert response.status_code == 307 or response.status_code == 302
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_auth_routes.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement auth routes**

`backend/auth/routes.py`:

```python
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, get_settings
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config import Settings

auth_router = APIRouter(tags=["auth"])


async def _get_oidc_client(settings: Settings) -> AsyncOAuth2Client:
    client = AsyncOAuth2Client(
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        redirect_uri=settings.oidc_redirect_uri,
    )
    return client


@auth_router.get("/login")
async def login(request: Request, app_settings: Settings = Depends(get_settings)):
    client = await _get_oidc_client(app_settings)
    authorization_url, state = client.create_authorization_url(
        f"{app_settings.oidc_issuer_url}/authorize"
    )
    request.session_state = state
    return RedirectResponse(url=authorization_url)


@auth_router.get("/callback")
async def callback(
    request: Request,
    code: str,
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
):
    client = await _get_oidc_client(app_settings)
    token_response = await client.fetch_token(
        f"{app_settings.oidc_issuer_url}/token",
        code=code,
        grant_type="authorization_code",
    )

    userinfo = await client.get(f"{app_settings.oidc_issuer_url}/userinfo")
    userinfo_data = userinfo.json()

    oidc_subject = userinfo_data.get("sub", "")
    name = userinfo_data.get("name", userinfo_data.get("preferred_username", "Unknown"))

    # Look up existing user by OIDC subject
    user = db.query(User).filter(User.oidc_subject == oidc_subject).first()

    if user is None:
        # Check if this is the first user (auto-admin)
        user_count = db.query(User).count()
        role = UserRole.ADMIN if user_count == 0 else UserRole.VIEWER

        # Generate a placeholder callsign from the OIDC subject
        # User can update this later via profile
        callsign = userinfo_data.get(
            "preferred_username", oidc_subject[:20]
        ).upper()

        user = User(
            callsign=callsign,
            oidc_subject=oidc_subject,
            name=name,
            role=role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token = create_access_token(
        user.callsign, user.role.value, app_settings
    )
    response = RedirectResponse(url=app_settings.app_base_url)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
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
    }


@auth_router.post("/logout")
async def logout():
    response = Response(content='{"message": "logged out"}', media_type="application/json")
    response.delete_cookie(key="access_token")
    return response
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_auth_routes.py -v"`

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/auth/routes.py tests/test_auth_routes.py
git commit -m "feat: add auth routes (login, callback, logout, me)"
```

---

### Task 6b: User Management Routes (Admin)

**Files:**
- Modify: `backend/auth/routes.py`
- Modify: `tests/test_auth_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth_routes.py`:

```python
@pytest.mark.asyncio
async def test_admin_can_list_users(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        user = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        session.add(user)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/auth/users", cookies={"access_token": token}
    )
    assert response.status_code == 200
    users = response.json()
    assert len(users) == 1
    assert users[0]["callsign"] == "W0NE"


@pytest.mark.asyncio
async def test_admin_can_update_user_role(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, viewer])
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
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, viewer])
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

Run: `nix-shell --run "pytest tests/test_auth_routes.py -v -k 'list_users or update_user_role or cannot_update'"`

Expected: FAIL — 404 (routes don't exist yet)

- [ ] **Step 3: Add user management routes to auth_router**

Append to `backend/auth/routes.py`:

```python
from pydantic import BaseModel


class UserRoleUpdate(BaseModel):
    role: str


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
    target_user.role = UserRole(body.role)
    db.commit()
    db.refresh(target_user)
    return {
        "callsign": target_user.callsign,
        "name": target_user.name,
        "role": target_user.role.value,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_auth_routes.py -v"`

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/auth/routes.py tests/test_auth_routes.py
git commit -m "feat: add user management routes (list users, update role)"
```

---

### Task 7: AppConfig Model

**Files:**
- Create: `backend/config_mgmt/__init__.py`
- Create: `backend/config_mgmt/models.py`
- Create: `backend/config_mgmt/service.py`
- Create: `tests/test_config_mgmt.py`

- [ ] **Step 1: Create config_mgmt package**

`backend/config_mgmt/__init__.py`:
```python
```

- [ ] **Step 2: Write the failing test**

`tests/test_config_mgmt.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.service import get_config_value, set_config_value, get_all_config


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_set_and_get_config(db: Session):
    set_config_value(db, "net_address", "w0ne@winlink.org")
    value = get_config_value(db, "net_address")
    assert value == "w0ne@winlink.org"


def test_get_nonexistent_config(db: Session):
    value = get_config_value(db, "nonexistent")
    assert value is None


def test_get_config_with_default(db: Session):
    value = get_config_value(db, "nonexistent", default="fallback")
    assert value == "fallback"


def test_update_existing_config(db: Session):
    set_config_value(db, "net_address", "old@winlink.org")
    set_config_value(db, "net_address", "new@winlink.org")
    value = get_config_value(db, "net_address")
    assert value == "new@winlink.org"


def test_get_all_config(db: Session):
    set_config_value(db, "net_address", "w0ne@winlink.org")
    set_config_value(db, "default_net_control", "W0NE")
    all_config = get_all_config(db)
    assert all_config == {
        "net_address": "w0ne@winlink.org",
        "default_net_control": "W0NE",
    }
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_config_mgmt.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 4: Implement AppConfig model and service**

`backend/config_mgmt/models.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
```

`backend/config_mgmt/service.py`:

```python
from sqlalchemy.orm import Session

from backend.config_mgmt.models import AppConfig


def get_config_value(db: Session, key: str, default: str | None = None) -> str | None:
    config = db.get(AppConfig, key)
    if config is None:
        return default
    return config.value


def set_config_value(db: Session, key: str, value: str) -> None:
    config = db.get(AppConfig, key)
    if config is None:
        config = AppConfig(key=key, value=value)
        db.add(config)
    else:
        config.value = value
    db.commit()


def get_all_config(db: Session) -> dict[str, str]:
    configs = db.query(AppConfig).all()
    return {c.key: c.value for c in configs}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_config_mgmt.py -v"`

Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/config_mgmt/__init__.py backend/config_mgmt/models.py backend/config_mgmt/service.py tests/test_config_mgmt.py
git commit -m "feat: add AppConfig model and config service"
```

---

### Task 8: Config API Routes

**Files:**
- Create: `backend/config_mgmt/routes.py`
- Create: `tests/test_config_routes.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config_routes.py`:

```python
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config_mgmt.routes import config_router
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def db_setup():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    # Seed admin user
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, viewer])
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(config_router, prefix="/api/config")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_admin_can_get_config(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/config/", cookies={"access_token": token}
    )
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


@pytest.mark.asyncio
async def test_admin_can_set_config(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.put(
        "/api/config/net_address",
        json={"value": "w0ne@winlink.org"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200

    response = await test_client.get(
        "/api/config/", cookies={"access_token": token}
    )
    assert response.json()["net_address"] == "w0ne@winlink.org"


@pytest.mark.asyncio
async def test_viewer_cannot_set_config(test_client, test_settings):
    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.put(
        "/api/config/net_address",
        json={"value": "hacker@winlink.org"},
        cookies={"access_token": token},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_get_config(test_client):
    response = await test_client.get("/api/config/")
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_config_routes.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement config routes**

`backend/config_mgmt/routes.py`:

```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, get_current_user, require_role
from backend.auth.models import User, UserRole
from backend.config_mgmt.service import get_all_config, set_config_value

config_router = APIRouter(tags=["config"])


class ConfigValueRequest(BaseModel):
    value: str


@config_router.get("/")
async def list_config(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    return get_all_config(db)


@config_router.put("/{key}")
async def update_config(
    key: str,
    body: ConfigValueRequest,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    set_config_value(db, key, body.value)
    return {"key": key, "value": body.value}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_config_routes.py -v"`

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/config_mgmt/routes.py tests/test_config_routes.py
git commit -m "feat: add config API routes (admin-only write)"
```

---

### Task 9: First Alembic Migration (Users + AppConfig)

**Files:**
- Create: `alembic/versions/001_add_users_and_config.py`
- Modify: `alembic/env.py`

- [ ] **Step 1: Update alembic/env.py to import all models**

Add model imports to `alembic/env.py` so autogenerate can detect them. Add these imports after the existing `from backend.db.base import Base` line:

```python
from backend.db.base import Base
# Import all models so Base.metadata includes their tables
import backend.auth.models  # noqa: F401
import backend.config_mgmt.models  # noqa: F401
```

- [ ] **Step 2: Auto-generate the migration**

Run: `nix-shell --run "alembic revision --autogenerate -m 'add users and app_config tables'"`

Expected: Creates a migration file in `alembic/versions/`

- [ ] **Step 3: Verify the migration has the correct tables**

Read the generated migration file. It should create `users` and `app_config` tables with the correct columns.

- [ ] **Step 4: Run the migration**

Run: `nix-shell --run "alembic upgrade head"`

Expected: Migration applies successfully

- [ ] **Step 5: Verify the tables exist**

Run: `nix-shell --run "python -c \"from sqlalchemy import create_engine, inspect; e = create_engine('sqlite:///skynetcontrol.db'); print(inspect(e).get_table_names())\""`

Expected: Output includes `users` and `app_config`

- [ ] **Step 6: Commit**

```bash
git add alembic/env.py alembic/versions/
git commit -m "feat: add migration for users and app_config tables"
```

---

### Task 10: NetSeason and NetSession Models

**Files:**
- Create: `backend/modules/schedule/__init__.py`
- Create: `backend/modules/__init__.py`
- Create: `backend/modules/schedule/models.py`
- Create: `tests/test_schedule_models.py`

- [ ] **Step 1: Create package files**

`backend/modules/__init__.py`:
```python
```

`backend/modules/schedule/__init__.py`:
```python
```

- [ ] **Step 2: Write the failing test**

`tests/test_schedule_models.py`:

```python
import pytest
from datetime import date, time
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_season(db: Session):
    season = NetSeason(
        name="Fall/Winter 2026",
        start_date=date(2026, 9, 7),
        end_date=date(2027, 5, 26),
        day_of_week=3,  # Thursday
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    fetched = db.get(NetSeason, season.id)
    assert fetched is not None
    assert fetched.name == "Fall/Winter 2026"
    assert fetched.day_of_week == 3
    assert fetched.activity_cadence == 2


def test_create_session(db: Session):
    season = NetSeason(
        name="Test Season",
        start_date=date(2026, 9, 7),
        end_date=date(2027, 5, 26),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    session_obj = NetSession(
        season_id=season.id,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 11),
        grace_period_hours=24,
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(session_obj)
    db.commit()

    fetched = db.get(NetSession, session_obj.id)
    assert fetched is not None
    assert fetched.session_type == SessionType.REGULAR_CHECKIN
    assert fetched.status == SessionStatus.SCHEDULED
    assert fetched.net_control_callsign == "W0NE"
    assert fetched.grace_period_hours == 24
    assert fetched.activity_id is None


def test_session_belongs_to_season(db: Session):
    season = NetSeason(
        name="Test",
        start_date=date(2026, 9, 7),
        end_date=date(2027, 5, 26),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    s1 = NetSession(
        season_id=season.id,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 11),
        grace_period_hours=24,
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(s1)
    db.commit()

    db.refresh(season)
    assert len(season.sessions) == 1
    assert season.sessions[0].id == s1.id
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_schedule_models.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 4: Implement schedule models**

`backend/modules/schedule/models.py`:

```python
import enum
from datetime import date, time

from sqlalchemy import (
    Integer,
    String,
    Date,
    Time,
    Boolean,
    Enum,
    ForeignKey,
    Float,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class SessionType(str, enum.Enum):
    REGULAR_CHECKIN = "regular_checkin"
    ACTIVITY = "activity"


class SessionStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class NetSeason(Base):
    __tablename__ = "net_seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_week_long: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activity_cadence: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    sessions: Mapped[list["NetSession"]] = relationship(
        back_populates="season", cascade="all, delete-orphan"
    )


class NetSession(Base):
    __tablename__ = "net_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(Integer, ForeignKey("net_seasons.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    grace_period_hours: Mapped[float] = mapped_column(Float, nullable=False, default=24.0)
    session_type: Mapped[SessionType] = mapped_column(Enum(SessionType), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), nullable=False, default=SessionStatus.SCHEDULED
    )
    activity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_control_callsign: Mapped[str | None] = mapped_column(String(20), nullable=True)

    season: Mapped["NetSeason"] = relationship(back_populates="sessions")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_schedule_models.py -v"`

Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add backend/modules/__init__.py backend/modules/schedule/__init__.py backend/modules/schedule/models.py tests/test_schedule_models.py
git commit -m "feat: add NetSeason and NetSession models"
```

---

### Task 11: Session Generation Service

**Files:**
- Create: `backend/modules/schedule/service.py`
- Create: `tests/test_schedule_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_schedule_service.py`:

```python
import pytest
from datetime import date, time
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)
from backend.modules.schedule.service import generate_sessions


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_generate_weekly_sessions(db: Session):
    season = NetSeason(
        name="Test Season",
        start_date=date(2026, 9, 3),  # Thursday
        end_date=date(2026, 10, 1),  # Thursday (4 weeks)
        day_of_week=3,  # Thursday (0=Monday)
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    sessions = generate_sessions(db, season, default_net_control="W0NE")

    assert len(sessions) == 5  # Sep 3, 10, 17, 24, Oct 1
    # First session is regular, second is activity, alternating
    assert sessions[0].session_type == SessionType.REGULAR_CHECKIN
    assert sessions[1].session_type == SessionType.ACTIVITY
    assert sessions[2].session_type == SessionType.REGULAR_CHECKIN
    assert sessions[3].session_type == SessionType.ACTIVITY
    assert sessions[4].session_type == SessionType.REGULAR_CHECKIN

    for s in sessions:
        assert s.status == SessionStatus.SCHEDULED
        assert s.net_control_callsign == "W0NE"
        assert s.season_id == season.id


def test_generate_sessions_correct_dates(db: Session):
    season = NetSeason(
        name="Short Season",
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 17),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    sessions = generate_sessions(db, season, default_net_control="W0NE")

    assert len(sessions) == 3
    assert sessions[0].start_date == date(2026, 9, 3)
    assert sessions[1].start_date == date(2026, 9, 10)
    assert sessions[2].start_date == date(2026, 9, 17)


def test_generate_sessions_default_grace_period(db: Session):
    season = NetSeason(
        name="Test",
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 3),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    sessions = generate_sessions(db, season, default_net_control="W0NE")

    assert len(sessions) == 1
    assert sessions[0].grace_period_hours == 24.0


def test_generate_week_long_sessions(db: Session):
    season = NetSeason(
        name="Summer 2026",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 21),
        day_of_week=None,
        time=None,
        is_week_long=True,
        activity_cadence=1,
    )
    db.add(season)
    db.commit()

    sessions = generate_sessions(db, season, default_net_control="W0NE")

    assert len(sessions) == 3  # 3 weeks: Jun 1-7, Jun 8-14, Jun 15-21
    assert sessions[0].start_date == date(2026, 6, 1)
    assert sessions[0].end_date == date(2026, 6, 7)
    assert sessions[1].start_date == date(2026, 6, 8)
    assert sessions[1].end_date == date(2026, 6, 14)
    assert sessions[2].start_date == date(2026, 6, 15)
    assert sessions[2].end_date == date(2026, 6, 21)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_schedule_service.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement session generation**

`backend/modules/schedule/service.py`:

```python
from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)


def generate_sessions(
    db: Session,
    season: NetSeason,
    default_net_control: str,
    default_grace_period_hours: float = 24.0,
) -> list[NetSession]:
    sessions: list[NetSession] = []

    if season.is_week_long:
        sessions = _generate_week_long_sessions(
            season, default_net_control, default_grace_period_hours
        )
    else:
        sessions = _generate_weekly_sessions(
            season, default_net_control, default_grace_period_hours
        )

    db.add_all(sessions)
    db.commit()
    for s in sessions:
        db.refresh(s)
    return sessions


def _generate_weekly_sessions(
    season: NetSeason,
    default_net_control: str,
    default_grace_period_hours: float,
) -> list[NetSession]:
    sessions: list[NetSession] = []
    current = season.start_date

    # Find the first occurrence of the target day of week
    if season.day_of_week is not None:
        while current.weekday() != season.day_of_week:
            current += timedelta(days=1)
        if current > season.end_date:
            return sessions

    index = 0
    while current <= season.end_date:
        session_type = (
            SessionType.ACTIVITY
            if season.activity_cadence > 0 and index % season.activity_cadence == 1
            else SessionType.REGULAR_CHECKIN
        )

        session = NetSession(
            season_id=season.id,
            start_date=current,
            end_date=current + timedelta(days=1),
            grace_period_hours=default_grace_period_hours,
            session_type=session_type,
            status=SessionStatus.SCHEDULED,
            net_control_callsign=default_net_control,
        )
        sessions.append(session)
        current += timedelta(weeks=1)
        index += 1

    return sessions


def _generate_week_long_sessions(
    season: NetSeason,
    default_net_control: str,
    default_grace_period_hours: float,
) -> list[NetSession]:
    sessions: list[NetSession] = []
    current = season.start_date
    index = 0

    while current <= season.end_date:
        week_end = current + timedelta(days=6)
        if week_end > season.end_date:
            week_end = season.end_date

        session_type = (
            SessionType.ACTIVITY
            if season.activity_cadence > 0 and index % season.activity_cadence == 1
            else SessionType.REGULAR_CHECKIN
        )

        session = NetSession(
            season_id=season.id,
            start_date=current,
            end_date=week_end,
            grace_period_hours=default_grace_period_hours,
            session_type=session_type,
            status=SessionStatus.SCHEDULED,
            net_control_callsign=default_net_control,
        )
        sessions.append(session)
        current = week_end + timedelta(days=1)
        index += 1

    return sessions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_schedule_service.py -v"`

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/schedule/service.py tests/test_schedule_service.py
git commit -m "feat: add session generation service"
```

---

### Task 12: Schedule API Routes

**Files:**
- Create: `backend/modules/schedule/routes.py`
- Create: `tests/test_schedule_api.py`

- [ ] **Step 1: Write the failing test**

`tests/test_schedule_api.py`:

```python
import pytest
from datetime import date
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.modules.schedule.routes import schedule_router
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def db_setup():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, viewer])
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(schedule_router, prefix="/api/schedule")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_season(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Fall 2026"
    assert data["id"] is not None
    assert len(data["sessions"]) == 5


@pytest.mark.asyncio
async def test_list_seasons(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    # Create a season first
    await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": token},
    )

    response = await test_client.get(
        "/api/schedule/seasons", cookies={"access_token": token}
    )
    assert response.status_code == 200
    seasons = response.json()
    assert len(seasons) == 1
    assert seasons[0]["name"] == "Fall 2026"


@pytest.mark.asyncio
async def test_list_sessions(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    create_resp = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": token},
    )
    season_id = create_resp.json()["id"]

    response = await test_client.get(
        f"/api/schedule/seasons/{season_id}/sessions",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 5


@pytest.mark.asyncio
async def test_update_session(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    create_resp = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": token},
    )
    session_id = create_resp.json()["sessions"][0]["id"]

    response = await test_client.patch(
        f"/api/schedule/sessions/{session_id}",
        json={"status": "cancelled"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_create(test_client, test_settings):
    admin_token = create_access_token("W0NE", "admin", test_settings)
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)

    # Create as admin
    await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": admin_token},
    )

    # Viewer can list
    response = await test_client.get(
        "/api/schedule/seasons", cookies={"access_token": viewer_token}
    )
    assert response.status_code == 200

    # Viewer cannot create
    response = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Hacked Season",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "day_of_week": 0,
            "time": "00:00",
            "is_week_long": False,
            "activity_cadence": 1,
        },
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_schedule_api.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement schedule routes**

`backend/modules/schedule/routes.py`:

```python
from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.config_mgmt.service import get_config_value
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionStatus,
    SessionType,
)
from backend.modules.schedule.service import generate_sessions

schedule_router = APIRouter(tags=["schedule"])


# --- Pydantic schemas ---


class SeasonCreate(BaseModel):
    name: str
    start_date: date
    end_date: date
    day_of_week: int | None = None
    time: str | None = None  # "HH:MM" format
    is_week_long: bool = False
    activity_cadence: int = 2


class SessionResponse(BaseModel):
    id: int
    start_date: date
    end_date: date
    grace_period_hours: float
    session_type: str
    status: str
    activity_id: int | None
    net_control_callsign: str | None

    model_config = {"from_attributes": True}


class SeasonResponse(BaseModel):
    id: int
    name: str
    start_date: date
    end_date: date
    day_of_week: int | None
    time: str | None
    is_week_long: bool
    activity_cadence: int
    sessions: list[SessionResponse] = []

    model_config = {"from_attributes": True}


class SessionUpdate(BaseModel):
    status: str | None = None
    session_type: str | None = None
    net_control_callsign: str | None = None
    activity_id: int | None = None
    grace_period_hours: float | None = None


# --- Helper ---


def _season_to_response(season: NetSeason) -> dict:
    return {
        "id": season.id,
        "name": season.name,
        "start_date": season.start_date.isoformat(),
        "end_date": season.end_date.isoformat(),
        "day_of_week": season.day_of_week,
        "time": season.time.strftime("%H:%M") if season.time else None,
        "is_week_long": season.is_week_long,
        "activity_cadence": season.activity_cadence,
        "sessions": [
            {
                "id": s.id,
                "start_date": s.start_date.isoformat(),
                "end_date": s.end_date.isoformat(),
                "grace_period_hours": s.grace_period_hours,
                "session_type": s.session_type.value,
                "status": s.status.value,
                "activity_id": s.activity_id,
                "net_control_callsign": s.net_control_callsign,
            }
            for s in season.sessions
        ],
    }


# --- Routes ---


@schedule_router.post("/seasons", status_code=201)
async def create_season(
    body: SeasonCreate,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    parsed_time = None
    if body.time:
        parts = body.time.split(":")
        parsed_time = time(int(parts[0]), int(parts[1]))

    season = NetSeason(
        name=body.name,
        start_date=body.start_date,
        end_date=body.end_date,
        day_of_week=body.day_of_week,
        time=parsed_time,
        is_week_long=body.is_week_long,
        activity_cadence=body.activity_cadence,
    )
    db.add(season)
    db.commit()
    db.refresh(season)

    default_net_control = get_config_value(db, "default_net_control", default="")
    generate_sessions(db, season, default_net_control=default_net_control or "")

    db.refresh(season)
    return _season_to_response(season)


@schedule_router.get("/seasons")
async def list_seasons(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    seasons = db.query(NetSeason).order_by(NetSeason.start_date.desc()).all()
    return [_season_to_response(s) for s in seasons]


@schedule_router.get("/seasons/{season_id}")
async def get_season(
    season_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    season = db.get(NetSeason, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return _season_to_response(season)


@schedule_router.get("/seasons/{season_id}/sessions")
async def list_sessions(
    season_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    season = db.get(NetSeason, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return [
        {
            "id": s.id,
            "start_date": s.start_date.isoformat(),
            "end_date": s.end_date.isoformat(),
            "grace_period_hours": s.grace_period_hours,
            "session_type": s.session_type.value,
            "status": s.status.value,
            "activity_id": s.activity_id,
            "net_control_callsign": s.net_control_callsign,
        }
        for s in season.sessions
    ]


@schedule_router.patch("/sessions/{session_id}")
async def update_session(
    session_id: int,
    body: SessionUpdate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    session_obj = db.get(NetSession, session_id)
    if session_obj is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if body.status is not None:
        session_obj.status = SessionStatus(body.status)
    if body.session_type is not None:
        session_obj.session_type = SessionType(body.session_type)
    if body.net_control_callsign is not None:
        session_obj.net_control_callsign = body.net_control_callsign
    if body.activity_id is not None:
        session_obj.activity_id = body.activity_id
    if body.grace_period_hours is not None:
        session_obj.grace_period_hours = body.grace_period_hours

    db.commit()
    db.refresh(session_obj)

    return {
        "id": session_obj.id,
        "start_date": session_obj.start_date.isoformat(),
        "end_date": session_obj.end_date.isoformat(),
        "grace_period_hours": session_obj.grace_period_hours,
        "session_type": session_obj.session_type.value,
        "status": session_obj.status.value,
        "activity_id": session_obj.activity_id,
        "net_control_callsign": session_obj.net_control_callsign,
    }


@schedule_router.delete("/seasons/{season_id}", status_code=204)
async def delete_season(
    season_id: int,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    season = db.get(NetSeason, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    db.delete(season)
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_schedule_api.py -v"`

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/schedule/routes.py tests/test_schedule_api.py
git commit -m "feat: add schedule API routes (seasons CRUD, session updates)"
```

---

### Task 13: Schedule Migration

**Files:**
- Modify: `alembic/env.py`
- Auto-generate migration

- [ ] **Step 1: Add schedule model imports to alembic/env.py**

Add after the existing model imports in `alembic/env.py`:

```python
import backend.modules.schedule.models  # noqa: F401
```

- [ ] **Step 2: Auto-generate migration**

Run: `nix-shell --run "alembic revision --autogenerate -m 'add schedule tables'"`

Expected: Creates a migration file with `net_seasons` and `net_sessions` tables

- [ ] **Step 3: Run the migration**

Run: `nix-shell --run "alembic upgrade head"`

Expected: Migration applies successfully

- [ ] **Step 4: Commit**

```bash
git add alembic/env.py alembic/versions/
git commit -m "feat: add migration for schedule tables"
```

---

### Task 14: Wire Everything Into app.py

**Files:**
- Modify: `backend/app.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Update backend/app.py to register all routers**

Replace `backend/app.py`:

```python
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.config import Settings, settings as default_settings
from backend.db.session import create_engine_from_url, create_session_factory
from backend.auth.routes import auth_router
from backend.config_mgmt.routes import config_router
from backend.modules.schedule.routes import schedule_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings
    app = FastAPI(title="SkyNetControl", version="0.1.0")

    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.settings = settings

    @app.get("/api/health")
    async def health():
        db_status = "disconnected"
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
                db_status = "connected"
        except Exception:
            pass
        return {"status": "ok", "version": "0.1.0", "database": db_status}

    # Register API routers
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(config_router, prefix="/api/config")
    app.include_router(schedule_router, prefix="/api/schedule")

    # Serve frontend static files if the directory exists
    if os.path.isdir(settings.static_dir):
        assets_dir = os.path.join(settings.static_dir, "assets")
        if os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{path:path}")
        async def serve_frontend(path: str):
            file_path = os.path.join(settings.static_dir, path)
            if path and os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(os.path.join(settings.static_dir, "index.html"))

    return app
```

- [ ] **Step 2: Update tests/conftest.py to create tables**

Replace `tests/conftest.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine

from backend.app import create_app
from backend.config import Settings
from backend.db.base import Base


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", debug=True)


@pytest.fixture
def app(test_settings):
    application = create_app(settings=test_settings)
    # Create all tables for tests
    Base.metadata.create_all(application.state.engine)
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] **Step 3: Run full test suite**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All tests pass (existing + new)

- [ ] **Step 4: Commit**

```bash
git add backend/app.py tests/conftest.py
git commit -m "feat: wire auth, config, and schedule routers into app"
```

---

### Task 15: Add Nix Dependencies

**Files:**
- Modify: `default.nix`

- [ ] **Step 1: Add authlib and python-jose to Nix dependencies**

Update the `dependencies` list in `default.nix` to include the new Python packages:

```nix
  dependencies = with python.pkgs; [
    fastapi
    uvicorn
    sqlalchemy
    alembic
    pydantic
    pydantic-settings
    authlib
    python-jose
    httpx
  ];
```

- [ ] **Step 2: Verify Nix build**

Run: `nix-build default.nix`

Expected: Builds successfully

- [ ] **Step 3: Commit**

```bash
git add default.nix
git commit -m "chore: add auth dependencies to Nix package"
```

---

### Task 16: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All tests pass

- [ ] **Step 2: Verify Nix build**

Run: `nix-build default.nix`

Expected: Builds successfully

- [ ] **Step 3: Clean up any test database files**

Run: `rm -f skynetcontrol.db`

- [ ] **Step 4: Tag the milestone**

```bash
git tag -a v0.1.0 -m "Phase 1: Auth, config, and schedule management"
```
