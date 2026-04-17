# Phase 2: Activities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the activity library with CRUD, tagging, usage tracking, and Claude AI chat integration for brainstorming new activities.

**Architecture:** Activity module under `backend/modules/activities/` following the same model/service/routes pattern as the schedule module. Claude chat integration via the anthropic Python SDK, with API key stored in AppConfig. Chat sessions are linked to activities on approval. Default "Standard Winlink Check-in" activity seeded via Alembic migration.

**Tech Stack:** SQLAlchemy models, anthropic SDK (Claude API), Alembic migrations, FastAPI routes

---

## File Structure

```
backend/
├── app.py                              # Modified: register activities router
├── modules/
│   └── activities/
│       ├── __init__.py
│       ├── models.py                   # Activity, ActivityTag, ActivityTagAssignment, ActivityUsage, ChatSession, ChatMessage
│       ├── service.py                  # Activity CRUD + tag management
│       ├── chat_service.py             # Claude chat integration
│       └── routes.py                   # /api/activities/* and /api/chat/* endpoints
alembic/
├── env.py                              # Modified: add activities model import
└── versions/
    └── 003_add_activities_and_chat.py  # New migration
tests/
├── test_activity_models.py
├── test_activity_service.py
├── test_activity_routes.py
├── test_chat_models.py
├── test_chat_service.py
└── test_chat_routes.py
pyproject.toml                          # Modified: add anthropic dependency
default.nix                             # Modified: add anthropic to Nix deps
# Note: Frontend UI for activities will be added in a separate phase.
# This plan covers backend API only.
```

---

### Task 1: Add Anthropic Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add anthropic SDK to pyproject.toml**

Add `anthropic` to the `dependencies` list in `pyproject.toml`:

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
    "anthropic>=0.42.0",
]
```

- [ ] **Step 2: Install updated dependencies**

Run: `nix-shell --run "pip install -e '.[dev]' --quiet && python -c 'import anthropic; print(anthropic.__version__)'"`

Expected: anthropic version printed

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add anthropic SDK dependency"
```

---

### Task 2: Activity and Tag Models

**Files:**
- Create: `backend/modules/activities/__init__.py`
- Create: `backend/modules/activities/models.py`
- Create: `tests/test_activity_models.py`

- [ ] **Step 1: Create activities package**

`backend/modules/activities/__init__.py`:
```python
```

- [ ] **Step 2: Write the failing test**

`tests/test_activity_models.py`:

```python
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.activities.models import (
    Activity,
    ActivityTag,
    ActivityTagAssignment,
    ActivityUsage,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_activity(db: Session):
    activity = Activity(
        title="Simplex HF Net Exercise",
        description="Practice simplex HF communications",
        instructions="# Instructions\n\nTune to 7.185 MHz...",
        is_default=False,
    )
    db.add(activity)
    db.commit()

    fetched = db.get(Activity, activity.id)
    assert fetched is not None
    assert fetched.title == "Simplex HF Net Exercise"
    assert fetched.is_default is False
    assert fetched.created_at is not None


def test_default_activity(db: Session):
    activity = Activity(
        title="Standard Winlink Check-in",
        description="Default check-in activity",
        instructions="Send a one-line check-in or use the Winlink net check-in form.",
        is_default=True,
    )
    db.add(activity)
    db.commit()

    fetched = db.get(Activity, activity.id)
    assert fetched is not None
    assert fetched.is_default is True


def test_create_tag(db: Session):
    tag = ActivityTag(name="HF")
    db.add(tag)
    db.commit()

    fetched = db.get(ActivityTag, tag.id)
    assert fetched is not None
    assert fetched.name == "HF"


def test_tag_name_is_unique(db: Session):
    tag1 = ActivityTag(name="HF")
    tag2 = ActivityTag(name="HF")
    db.add(tag1)
    db.commit()
    db.add(tag2)
    with pytest.raises(Exception):
        db.commit()


def test_activity_tag_assignment(db: Session):
    activity = Activity(
        title="Test Activity",
        description="Test",
        instructions="Test instructions",
    )
    tag = ActivityTag(name="beginner-friendly")
    db.add_all([activity, tag])
    db.commit()

    assignment = ActivityTagAssignment(
        activity_id=activity.id, tag_id=tag.id
    )
    db.add(assignment)
    db.commit()

    db.refresh(activity)
    assert len(activity.tags) == 1
    assert activity.tags[0].name == "beginner-friendly"


def test_activity_usage(db: Session):
    activity = Activity(
        title="Test Activity",
        description="Test",
        instructions="Test instructions",
    )
    db.add(activity)
    db.commit()

    usage = ActivityUsage(
        activity_id=activity.id,
        session_id=1,
    )
    db.add(usage)
    db.commit()

    fetched = db.get(ActivityUsage, usage.id)
    assert fetched is not None
    assert fetched.activity_id == activity.id
    assert fetched.session_id == 1
    assert fetched.used_at is not None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_activity_models.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 4: Implement models**

`backend/modules/activities/models.py`:

```python
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tags: Mapped[list["ActivityTag"]] = relationship(
        secondary="activity_tag_assignments",
        back_populates="activities",
    )
    usages: Mapped[list["ActivityUsage"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="activity",
    )


class ActivityTag(Base):
    __tablename__ = "activity_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    activities: Mapped[list["Activity"]] = relationship(
        secondary="activity_tag_assignments",
        back_populates="tags",
    )


class ActivityTagAssignment(Base):
    __tablename__ = "activity_tag_assignments"

    activity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("activities.id"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("activity_tags.id"), primary_key=True
    )


class ActivityUsage(Base):
    __tablename__ = "activity_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("activities.id"), nullable=False
    )
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("net_sessions.id"), nullable=False
    )
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    activity: Mapped["Activity"] = relationship(back_populates="usages")


class ChatMessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("activities.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    activity: Mapped["Activity | None"] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="chat_session", cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_sessions.id"), nullable=False
    )
    role: Mapped[ChatMessageRole] = mapped_column(
        Enum(ChatMessageRole), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    chat_session: Mapped["ChatSession"] = relationship(back_populates="messages")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_activity_models.py -v"`

Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add backend/modules/activities/__init__.py backend/modules/activities/models.py tests/test_activity_models.py
git commit -m "feat: add Activity, Tag, Usage, and Chat models"
```

---

### Task 3: Activity CRUD Service

**Files:**
- Create: `backend/modules/activities/service.py`
- Create: `tests/test_activity_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_activity_service.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.activities.models import Activity, ActivityTag
from backend.modules.activities.service import (
    create_activity,
    get_activity,
    list_activities,
    update_activity,
    delete_activity,
    get_or_create_tags,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_activity(db: Session):
    activity = create_activity(
        db,
        title="Test Activity",
        description="A test",
        instructions="Do the thing",
        tag_names=["HF", "beginner-friendly"],
    )
    assert activity.id is not None
    assert activity.title == "Test Activity"
    assert len(activity.tags) == 2
    tag_names = {t.name for t in activity.tags}
    assert tag_names == {"HF", "beginner-friendly"}


def test_create_activity_no_tags(db: Session):
    activity = create_activity(
        db,
        title="Simple Activity",
        description="Simple",
        instructions="Just check in",
    )
    assert activity.id is not None
    assert len(activity.tags) == 0


def test_list_activities(db: Session):
    create_activity(db, title="A1", description="d", instructions="i")
    create_activity(db, title="A2", description="d", instructions="i")
    activities = list_activities(db)
    assert len(activities) == 2


def test_get_activity(db: Session):
    created = create_activity(db, title="Find Me", description="d", instructions="i")
    found = get_activity(db, created.id)
    assert found is not None
    assert found.title == "Find Me"


def test_get_activity_not_found(db: Session):
    found = get_activity(db, 999)
    assert found is None


def test_update_activity(db: Session):
    activity = create_activity(
        db, title="Old Title", description="old", instructions="old"
    )
    updated = update_activity(
        db,
        activity.id,
        title="New Title",
        description="new",
        tag_names=["VHF"],
    )
    assert updated is not None
    assert updated.title == "New Title"
    assert updated.description == "new"
    assert updated.instructions == "old"  # not updated
    assert len(updated.tags) == 1
    assert updated.tags[0].name == "VHF"


def test_delete_activity(db: Session):
    activity = create_activity(db, title="Delete Me", description="d", instructions="i")
    result = delete_activity(db, activity.id)
    assert result is True
    assert get_activity(db, activity.id) is None


def test_cannot_delete_default_activity(db: Session):
    activity = create_activity(
        db, title="Default", description="d", instructions="i", is_default=True
    )
    result = delete_activity(db, activity.id)
    assert result is False
    assert get_activity(db, activity.id) is not None


def test_get_or_create_tags_reuses_existing(db: Session):
    tag = ActivityTag(name="HF")
    db.add(tag)
    db.commit()

    tags = get_or_create_tags(db, ["HF", "VHF"])
    assert len(tags) == 2
    hf_tag = next(t for t in tags if t.name == "HF")
    assert hf_tag.id == tag.id  # reused, not duplicated
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_activity_service.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement activity service**

`backend/modules/activities/service.py`:

```python
from sqlalchemy.orm import Session

from backend.modules.activities.models import Activity, ActivityTag


def get_or_create_tags(db: Session, tag_names: list[str]) -> list[ActivityTag]:
    if not tag_names:
        return []
    tags = []
    for name in tag_names:
        tag = db.query(ActivityTag).filter(ActivityTag.name == name).first()
        if tag is None:
            tag = ActivityTag(name=name)
            db.add(tag)
        tags.append(tag)
    db.flush()
    return tags


def create_activity(
    db: Session,
    title: str,
    description: str,
    instructions: str,
    tag_names: list[str] | None = None,
    is_default: bool = False,
) -> Activity:
    activity = Activity(
        title=title,
        description=description,
        instructions=instructions,
        is_default=is_default,
    )
    if tag_names:
        activity.tags = get_or_create_tags(db, tag_names)
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def get_activity(db: Session, activity_id: int) -> Activity | None:
    return db.get(Activity, activity_id)


def list_activities(db: Session) -> list[Activity]:
    return db.query(Activity).order_by(Activity.title).all()


def update_activity(
    db: Session,
    activity_id: int,
    title: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    tag_names: list[str] | None = None,
) -> Activity | None:
    activity = db.get(Activity, activity_id)
    if activity is None:
        return None

    if title is not None:
        activity.title = title
    if description is not None:
        activity.description = description
    if instructions is not None:
        activity.instructions = instructions
    if tag_names is not None:
        activity.tags = get_or_create_tags(db, tag_names)

    db.commit()
    db.refresh(activity)
    return activity


def delete_activity(db: Session, activity_id: int) -> bool:
    activity = db.get(Activity, activity_id)
    if activity is None:
        return False
    if activity.is_default:
        return False
    db.delete(activity)
    db.commit()
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_activity_service.py -v"`

Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/activities/service.py tests/test_activity_service.py
git commit -m "feat: add activity CRUD service with tag management"
```

---

### Task 4: Activity API Routes

**Files:**
- Create: `backend/modules/activities/routes.py`
- Create: `tests/test_activity_routes.py`

- [ ] **Step 1: Write the failing test**

`tests/test_activity_routes.py`:

```python
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.modules.activities.routes import activities_router
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
    engine = create_engine(
        "sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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
    app.include_router(activities_router, prefix="/api/activities")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/activities/",
        json={
            "title": "Simplex Exercise",
            "description": "Practice simplex",
            "instructions": "Tune to 7.185 MHz",
            "tag_names": ["HF", "beginner-friendly"],
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Simplex Exercise"
    assert data["id"] is not None
    assert len(data["tags"]) == 2


@pytest.mark.asyncio
async def test_list_activities(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    await test_client.post(
        "/api/activities/",
        json={
            "title": "Activity 1",
            "description": "d",
            "instructions": "i",
        },
        cookies={"access_token": token},
    )

    response = await test_client.get(
        "/api/activities/", cookies={"access_token": token}
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_get_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    create_resp = await test_client.post(
        "/api/activities/",
        json={"title": "Find Me", "description": "d", "instructions": "i"},
        cookies={"access_token": token},
    )
    activity_id = create_resp.json()["id"]

    response = await test_client.get(
        f"/api/activities/{activity_id}", cookies={"access_token": token}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Find Me"


@pytest.mark.asyncio
async def test_update_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    create_resp = await test_client.post(
        "/api/activities/",
        json={"title": "Old", "description": "old", "instructions": "old"},
        cookies={"access_token": token},
    )
    activity_id = create_resp.json()["id"]

    response = await test_client.patch(
        f"/api/activities/{activity_id}",
        json={"title": "New", "tag_names": ["VHF"]},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "New"
    assert len(response.json()["tags"]) == 1


@pytest.mark.asyncio
async def test_delete_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    create_resp = await test_client.post(
        "/api/activities/",
        json={"title": "Delete Me", "description": "d", "instructions": "i"},
        cookies={"access_token": token},
    )
    activity_id = create_resp.json()["id"]

    response = await test_client.delete(
        f"/api/activities/{activity_id}", cookies={"access_token": token}
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_cannot_delete_default_activity(test_client, test_settings, db_setup):
    # Seed a default activity directly
    from backend.modules.activities.models import Activity
    with db_setup() as session:
        activity = Activity(
            title="Default",
            description="Default check-in",
            instructions="Check in",
            is_default=True,
        )
        session.add(activity)
        session.commit()
        activity_id = activity.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.delete(
        f"/api/activities/{activity_id}", cookies={"access_token": token}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_create(test_client, test_settings):
    admin_token = create_access_token("W0NE", "admin", test_settings)
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)

    # Create as admin
    await test_client.post(
        "/api/activities/",
        json={"title": "Activity", "description": "d", "instructions": "i"},
        cookies={"access_token": admin_token},
    )

    # Viewer can list
    response = await test_client.get(
        "/api/activities/", cookies={"access_token": viewer_token}
    )
    assert response.status_code == 200

    # Viewer cannot create
    response = await test_client.post(
        "/api/activities/",
        json={"title": "Hack", "description": "d", "instructions": "i"},
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_tags(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    await test_client.post(
        "/api/activities/",
        json={
            "title": "Tagged",
            "description": "d",
            "instructions": "i",
            "tag_names": ["HF", "VHF"],
        },
        cookies={"access_token": token},
    )

    response = await test_client.get(
        "/api/activities/tags", cookies={"access_token": token}
    )
    assert response.status_code == 200
    tags = response.json()
    assert len(tags) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_activity_routes.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement activity routes**

`backend/modules/activities/routes.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.modules.activities.models import Activity, ActivityTag
from backend.modules.activities.service import (
    create_activity,
    delete_activity,
    get_activity,
    list_activities,
    update_activity,
)

activities_router = APIRouter(tags=["activities"])


# --- Pydantic schemas ---


class ActivityCreate(BaseModel):
    title: str
    description: str
    instructions: str
    tag_names: list[str] = []


class ActivityUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    instructions: str | None = None
    tag_names: list[str] | None = None


class TagResponse(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


# --- Helpers ---


def _activity_to_response(activity: Activity) -> dict:
    return {
        "id": activity.id,
        "title": activity.title,
        "description": activity.description,
        "instructions": activity.instructions,
        "is_default": activity.is_default,
        "created_at": activity.created_at.isoformat(),
        "last_used_at": activity.last_used_at.isoformat() if activity.last_used_at else None,
        "tags": [{"id": t.id, "name": t.name} for t in activity.tags],
    }


# --- Routes ---


@activities_router.post("/", status_code=201)
async def create_activity_route(
    body: ActivityCreate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    activity = create_activity(
        db,
        title=body.title,
        description=body.description,
        instructions=body.instructions,
        tag_names=body.tag_names,
    )
    return _activity_to_response(activity)


@activities_router.get("/")
async def list_activities_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    activities = list_activities(db)
    return [_activity_to_response(a) for a in activities]


@activities_router.get("/tags")
async def list_tags_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    tags = db.query(ActivityTag).order_by(ActivityTag.name).all()
    return [{"id": t.id, "name": t.name} for t in tags]


@activities_router.get("/{activity_id}")
async def get_activity_route(
    activity_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    activity = get_activity(db, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return _activity_to_response(activity)


@activities_router.patch("/{activity_id}")
async def update_activity_route(
    activity_id: int,
    body: ActivityUpdate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    activity = update_activity(
        db,
        activity_id,
        title=body.title,
        description=body.description,
        instructions=body.instructions,
        tag_names=body.tag_names,
    )
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return _activity_to_response(activity)


@activities_router.delete("/{activity_id}", status_code=204)
async def delete_activity_route(
    activity_id: int,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    activity = get_activity(db, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    if activity.is_default:
        raise HTTPException(status_code=403, detail="Cannot delete the default activity")
    delete_activity(db, activity_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_activity_routes.py -v"`

Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/activities/routes.py tests/test_activity_routes.py
git commit -m "feat: add activity API routes (CRUD, tags)"
```

---

### Task 5: Chat Models Tests

**Files:**
- Create: `tests/test_chat_models.py`

- [ ] **Step 1: Write the test**

`tests/test_chat_models.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.activities.models import (
    Activity,
    ChatSession,
    ChatMessage,
    ChatMessageRole,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_chat_session(db: Session):
    chat = ChatSession()
    db.add(chat)
    db.commit()

    fetched = db.get(ChatSession, chat.id)
    assert fetched is not None
    assert fetched.activity_id is None
    assert fetched.created_at is not None


def test_chat_messages(db: Session):
    chat = ChatSession()
    db.add(chat)
    db.commit()

    msg1 = ChatMessage(
        chat_session_id=chat.id,
        role=ChatMessageRole.USER,
        content="I want an activity about emergency prep",
    )
    msg2 = ChatMessage(
        chat_session_id=chat.id,
        role=ChatMessageRole.ASSISTANT,
        content="How about a simulated emergency net exercise?",
    )
    db.add_all([msg1, msg2])
    db.commit()

    db.refresh(chat)
    assert len(chat.messages) == 2
    assert chat.messages[0].role == ChatMessageRole.USER
    assert chat.messages[1].role == ChatMessageRole.ASSISTANT


def test_link_chat_to_activity(db: Session):
    activity = Activity(
        title="Emergency Prep",
        description="Practice emergency communications",
        instructions="Set up go-kit and check in",
    )
    db.add(activity)
    db.commit()

    chat = ChatSession(activity_id=activity.id)
    db.add(chat)
    db.commit()

    db.refresh(chat)
    assert chat.activity is not None
    assert chat.activity.title == "Emergency Prep"

    db.refresh(activity)
    assert len(activity.chat_sessions) == 1
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_chat_models.py -v"`

Expected: 3 passed (models already implemented in Task 2)

- [ ] **Step 3: Commit**

```bash
git add tests/test_chat_models.py
git commit -m "test: add chat model tests"
```

---

### Task 6: Claude Chat Service

**Files:**
- Create: `backend/modules/activities/chat_service.py`
- Create: `tests/test_chat_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_chat_service.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.activities.models import (
    Activity,
    ChatSession,
    ChatMessage,
    ChatMessageRole,
)
from backend.modules.activities.chat_service import (
    create_chat_session,
    send_message,
    link_chat_to_activity,
    get_chat_session,
    get_chat_history,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_chat_session(db: Session):
    chat = create_chat_session(db)
    assert chat.id is not None
    assert chat.activity_id is None
    assert len(chat.messages) == 0


def test_send_message_stores_user_message(db: Session):
    chat = create_chat_session(db)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Here's an activity idea: Emergency Net Drill")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        user_msg, assistant_msg = send_message(
            db, chat.id, "I want an emergency prep activity", api_key="test-key"
        )

    assert user_msg.role == ChatMessageRole.USER
    assert user_msg.content == "I want an emergency prep activity"
    assert assistant_msg.role == ChatMessageRole.ASSISTANT
    assert "Emergency Net Drill" in assistant_msg.content


def test_send_message_passes_history(db: Session):
    chat = create_chat_session(db)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Response 1")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        send_message(db, chat.id, "First message", api_key="test-key")

    mock_response2 = MagicMock()
    mock_response2.content = [MagicMock(text="Response 2")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response2
        send_message(db, chat.id, "Second message", api_key="test-key")

        # Verify Claude was called with full history (2 user msgs + 1 assistant msg)
        call_args = mock_claude.call_args
        messages = call_args[1]["messages"]
        assert len(messages) == 3  # user, assistant, user


def test_link_chat_to_activity(db: Session):
    chat = create_chat_session(db)
    activity = Activity(
        title="Linked Activity",
        description="d",
        instructions="i",
    )
    db.add(activity)
    db.commit()

    link_chat_to_activity(db, chat.id, activity.id)

    db.refresh(chat)
    assert chat.activity_id == activity.id


def test_get_chat_session(db: Session):
    chat = create_chat_session(db)
    found = get_chat_session(db, chat.id)
    assert found is not None
    assert found.id == chat.id


def test_get_chat_history(db: Session):
    chat = create_chat_session(db)
    msg = ChatMessage(
        chat_session_id=chat.id,
        role=ChatMessageRole.USER,
        content="Hello",
    )
    db.add(msg)
    db.commit()

    messages = get_chat_history(db, chat.id)
    assert len(messages) == 1
    assert messages[0].content == "Hello"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_chat_service.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement chat service**

`backend/modules/activities/chat_service.py`:

```python
import anthropic

from sqlalchemy.orm import Session

from backend.modules.activities.models import (
    ChatMessage,
    ChatMessageRole,
    ChatSession,
)

SYSTEM_PROMPT = """You are a helpful assistant for a ham radio Winlink net manager. \
You help brainstorm and design activities for weekly net sessions. \
Activities should be fun, educational, and practical for amateur radio operators. \
When suggesting an activity, provide a clear title, brief description, and \
detailed instructions in markdown format that can be sent to participants."""


def _call_claude(
    api_key: str,
    messages: list[dict],
    model: str = "claude-sonnet-4-20250514",
) -> anthropic.types.Message:
    client = anthropic.Anthropic(api_key=api_key)
    return client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )


def create_chat_session(db: Session) -> ChatSession:
    chat = ChatSession()
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def get_chat_session(db: Session, chat_session_id: int) -> ChatSession | None:
    return db.get(ChatSession, chat_session_id)


def get_chat_history(db: Session, chat_session_id: int) -> list[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.chat_session_id == chat_session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )


def send_message(
    db: Session,
    chat_session_id: int,
    user_content: str,
    api_key: str,
) -> tuple[ChatMessage, ChatMessage]:
    # Get existing history
    history = get_chat_history(db, chat_session_id)
    messages = [{"role": m.role.value, "content": m.content} for m in history]
    messages.append({"role": "user", "content": user_content})

    # Save user message
    user_msg = ChatMessage(
        chat_session_id=chat_session_id,
        role=ChatMessageRole.USER,
        content=user_content,
    )
    db.add(user_msg)
    db.flush()

    # Call Claude
    response = _call_claude(api_key=api_key, messages=messages)
    assistant_content = response.content[0].text

    # Save assistant message
    assistant_msg = ChatMessage(
        chat_session_id=chat_session_id,
        role=ChatMessageRole.ASSISTANT,
        content=assistant_content,
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(user_msg)
    db.refresh(assistant_msg)

    return user_msg, assistant_msg


def link_chat_to_activity(
    db: Session, chat_session_id: int, activity_id: int
) -> None:
    chat = db.get(ChatSession, chat_session_id)
    if chat is not None:
        chat.activity_id = activity_id
        db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_chat_service.py -v"`

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/activities/chat_service.py tests/test_chat_service.py
git commit -m "feat: add Claude chat service for activity brainstorming"
```

---

### Task 7: Chat API Routes

**Files:**
- Modify: `backend/modules/activities/routes.py`
- Create: `tests/test_chat_routes.py`

- [ ] **Step 1: Write the failing test**

`tests/test_chat_routes.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config_mgmt.models import AppConfig
from backend.modules.activities.routes import activities_router
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
    engine = create_engine(
        "sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        session.add(admin)
        # Seed Claude API key in AppConfig
        config = AppConfig(key="claude_api_key", value="test-key-123")
        session.add(config)
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(activities_router, prefix="/api/activities")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_chat_session(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["activity_id"] is None
    assert data["messages"] == []


@pytest.mark.asyncio
async def test_send_chat_message(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)

    # Create session
    create_resp = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    chat_id = create_resp.json()["id"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Great idea! How about a simplex exercise?")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        response = await test_client.post(
            f"/api/activities/chat/sessions/{chat_id}/messages",
            json={"content": "I want an HF activity"},
            cookies={"access_token": token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["user_message"]["content"] == "I want an HF activity"
    assert "simplex exercise" in data["assistant_message"]["content"]


@pytest.mark.asyncio
async def test_get_chat_session_with_messages(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)

    create_resp = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    chat_id = create_resp.json()["id"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Sure, here's an idea")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        await test_client.post(
            f"/api/activities/chat/sessions/{chat_id}/messages",
            json={"content": "Hello"},
            cookies={"access_token": token},
        )

    response = await test_client.get(
        f"/api/activities/chat/sessions/{chat_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 2


@pytest.mark.asyncio
async def test_approve_chat_creates_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)

    create_resp = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    chat_id = create_resp.json()["id"]

    response = await test_client.post(
        f"/api/activities/chat/sessions/{chat_id}/approve",
        json={
            "title": "Emergency Prep",
            "description": "Practice emergency comms",
            "instructions": "Set up your go-kit",
            "tag_names": ["emergency-prep"],
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Emergency Prep"
    assert data["id"] is not None

    # Verify chat session is linked to activity
    chat_resp = await test_client.get(
        f"/api/activities/chat/sessions/{chat_id}",
        cookies={"access_token": token},
    )
    assert chat_resp.json()["activity_id"] == data["id"]


@pytest.mark.asyncio
async def test_send_message_without_api_key(test_client, test_settings, db_setup):
    # Remove the API key from config
    with db_setup() as session:
        config = session.get(AppConfig, "claude_api_key")
        if config:
            session.delete(config)
            session.commit()

    token = create_access_token("W0NE", "admin", test_settings)

    create_resp = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    chat_id = create_resp.json()["id"]

    response = await test_client.post(
        f"/api/activities/chat/sessions/{chat_id}/messages",
        json={"content": "Hello"},
        cookies={"access_token": token},
    )
    assert response.status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_chat_routes.py -v"`

Expected: FAIL — ImportError or 404

- [ ] **Step 3: Add chat routes to activities_router**

Append to `backend/modules/activities/routes.py`:

```python
from backend.config_mgmt.service import get_config_value
from backend.modules.activities.chat_service import (
    create_chat_session,
    get_chat_history,
    get_chat_session,
    link_chat_to_activity,
    send_message,
)
from backend.modules.activities.models import ChatMessageRole


class ChatMessageRequest(BaseModel):
    content: str


class ChatApproveRequest(BaseModel):
    title: str
    description: str
    instructions: str
    tag_names: list[str] = []


def _chat_session_to_response(chat: "ChatSession", messages: list | None = None) -> dict:
    from backend.modules.activities.models import ChatSession as CS
    msgs = messages if messages is not None else (chat.messages if chat.messages else [])
    return {
        "id": chat.id,
        "activity_id": chat.activity_id,
        "created_at": chat.created_at.isoformat(),
        "messages": [
            {
                "id": m.id,
                "role": m.role.value,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
    }


def _message_to_response(msg) -> dict:
    return {
        "id": msg.id,
        "role": msg.role.value,
        "content": msg.content,
        "created_at": msg.created_at.isoformat(),
    }


@activities_router.post("/chat/sessions", status_code=201)
async def create_chat_session_route(
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    chat = create_chat_session(db)
    return _chat_session_to_response(chat)


@activities_router.get("/chat/sessions/{chat_session_id}")
async def get_chat_session_route(
    chat_session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    chat = get_chat_session(db, chat_session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    messages = get_chat_history(db, chat_session_id)
    return _chat_session_to_response(chat, messages)


@activities_router.post("/chat/sessions/{chat_session_id}/messages")
async def send_chat_message_route(
    chat_session_id: int,
    body: ChatMessageRequest,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    chat = get_chat_session(db, chat_session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    api_key = get_config_value(db, "claude_api_key")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Claude API key not configured. Set 'claude_api_key' in app config.",
        )

    user_msg, assistant_msg = send_message(
        db, chat_session_id, body.content, api_key=api_key
    )
    return {
        "user_message": _message_to_response(user_msg),
        "assistant_message": _message_to_response(assistant_msg),
    }


@activities_router.post("/chat/sessions/{chat_session_id}/approve", status_code=201)
async def approve_chat_route(
    chat_session_id: int,
    body: ChatApproveRequest,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    chat = get_chat_session(db, chat_session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    activity = create_activity(
        db,
        title=body.title,
        description=body.description,
        instructions=body.instructions,
        tag_names=body.tag_names,
    )
    link_chat_to_activity(db, chat_session_id, activity.id)
    return _activity_to_response(activity)
```

Note: The new imports (`get_config_value`, chat service functions, `ChatMessageRole`) should be added at the top of the file alongside existing imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_chat_routes.py -v"`

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/activities/routes.py tests/test_chat_routes.py
git commit -m "feat: add Claude chat API routes for activity brainstorming"
```

---

### Task 8: Default Activity Seed Migration

**Files:**
- Modify: `alembic/env.py`
- Auto-generate migration

- [ ] **Step 1: Add activities model import to alembic/env.py**

Add after existing model imports in `alembic/env.py`:

```python
import backend.modules.activities.models  # noqa: F401
```

- [ ] **Step 2: Auto-generate the migration**

Run: `nix-shell --run "alembic revision --autogenerate -m 'add activities and chat tables'"`

Expected: Creates a migration file with `activities`, `activity_tags`, `activity_tag_assignments`, `activity_usages`, `chat_sessions`, and `chat_messages` tables

- [ ] **Step 3: Verify the migration has the correct tables**

Read the generated migration file. It should create all 6 tables with correct columns and foreign keys.

- [ ] **Step 4: Add default activity seed to the migration**

After the `upgrade()` function's table creation, add data seeding. Edit the generated migration file to append to the `upgrade()` function:

```python
    # Seed default activity
    op.execute(
        "INSERT INTO activities (title, description, instructions, is_default) "
        "VALUES ("
        "'Standard Winlink Check-in', "
        "'Default check-in activity for regular net sessions', "
        "'Send a one-line check-in message or use the Winlink net check-in form. "
        "Include your name, callsign, city, county, state, and mode.', "
        "1)"
    )
```

- [ ] **Step 5: Run the migration**

Run: `nix-shell --run "alembic upgrade head"`

Expected: Migration applies successfully

- [ ] **Step 6: Verify the default activity exists**

Run: `nix-shell --run "python -c \"from sqlalchemy import create_engine, text; e = create_engine('sqlite:///skynetcontrol.db'); r = e.connect().execute(text('SELECT title, is_default FROM activities')); print(list(r))\""`

Expected: `[('Standard Winlink Check-in', 1)]`

- [ ] **Step 7: Commit**

```bash
git add alembic/env.py alembic/versions/
git commit -m "feat: add migration for activities/chat tables with default activity seed"
```

---

### Task 9: Wire Into app.py and Update Nix

**Files:**
- Modify: `backend/app.py`
- Modify: `default.nix`

- [ ] **Step 1: Register activities router in app.py**

Add import at the top of `backend/app.py` after the existing router imports:

```python
from backend.modules.activities.routes import activities_router
```

Add router registration after the existing `include_router` calls:

```python
    app.include_router(activities_router, prefix="/api/activities")
```

- [ ] **Step 2: Add anthropic to Nix dependencies**

Update the `dependencies` list in `default.nix` to include `anthropic`:

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
    anthropic
  ];
```

- [ ] **Step 3: Run full test suite**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All tests pass (existing 46 + new activity/chat tests)

- [ ] **Step 4: Verify Nix build**

Run: `nix-build default.nix`

Expected: Builds successfully

- [ ] **Step 5: Commit**

```bash
git add backend/app.py default.nix
git commit -m "feat: wire activities module into app, add anthropic to Nix"
```

---

### Task 10: Final Verification

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
git tag -a v0.2.0 -m "Phase 2: Activities with Claude chat integration"
```
