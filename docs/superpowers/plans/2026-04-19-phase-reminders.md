# Module 3: Reminders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reminder workflow with Jinja2 templates, idempotent draft generation, and an approve/send lifecycle for net session reminders.

**Architecture:** Follows the existing `models.py / service.py / routes.py` module pattern. Templates are rendered with Jinja2 using session/season/activity context. Draft generation is triggered by API call (manual or external cron). Groups.io posting is deferred — "send" just marks status.

**Tech Stack:** FastAPI, SQLAlchemy 2.0+, Jinja2, Alembic, Pydantic

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/modules/reminders/__init__.py` | Package marker |
| `backend/modules/reminders/models.py` | ReminderTemplate, ReminderLog, TemplateType, ReminderStatus |
| `backend/modules/reminders/service.py` | Template CRUD, Jinja2 rendering, draft generation, status transitions |
| `backend/modules/reminders/routes.py` | API endpoints, Pydantic schemas |
| `tests/test_reminder_models.py` | Model creation and constraints |
| `tests/test_reminder_service.py` | Service logic: rendering, draft generation, status transitions |
| `tests/test_reminder_routes.py` | API endpoint tests with auth |
| `alembic/versions/*_add_reminders_tables.py` | Migration for reminder_templates and reminder_logs |
| `alembic/env.py` | Add reminders model import |
| `backend/app.py` | Register reminders_router |

---

### Task 1: ReminderTemplate and ReminderLog Models

**Files:**
- Create: `backend/modules/reminders/__init__.py`
- Create: `backend/modules/reminders/models.py`
- Create: `tests/test_reminder_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_reminder_models.py`:

```python
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
import backend.modules.schedule.models  # ensure schedule tables exist
import backend.modules.reminders.models  # ensure reminders tables exist
from backend.modules.reminders.models import (
    ReminderTemplate,
    ReminderLog,
    TemplateType,
    ReminderStatus,
)
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)
from datetime import date, time


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def season_and_session(db):
    season = NetSeason(
        name="Test Season",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 6, 30),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season)
    db.flush()

    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session)
    db.commit()
    return season, net_session


def test_create_reminder_template(db):
    template = ReminderTemplate(
        name="Regular Reminder",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net Reminder — {{ date }}",
        body_template="Reminder for {{ date }}. Net control: {{ net_control }}",
        lead_time_days=2,
        is_default=True,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    assert template.id is not None
    assert template.template_type == TemplateType.REGULAR_CHECKIN
    assert template.is_default is True
    assert template.lead_time_days == 2


def test_template_name_is_unique(db):
    t1 = ReminderTemplate(
        name="Same Name",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s",
        body_template="b",
    )
    t2 = ReminderTemplate(
        name="Same Name",
        template_type=TemplateType.ACTIVITY,
        subject_template="s",
        body_template="b",
    )
    db.add(t1)
    db.commit()
    db.add(t2)
    with pytest.raises(Exception):
        db.commit()


def test_create_reminder_log(db, season_and_session):
    _, net_session = season_and_session
    template = ReminderTemplate(
        name="Test Template",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s",
        body_template="b",
    )
    db.add(template)
    db.flush()

    log = ReminderLog(
        session_id=net_session.id,
        template_id=template.id,
        status=ReminderStatus.DRAFT,
        content_subject="Rendered subject",
        content_body="Rendered body",
        drafted_at=datetime.now(timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    assert log.id is not None
    assert log.status == ReminderStatus.DRAFT
    assert log.approved_at is None
    assert log.approved_by is None


def test_reminder_log_session_id_is_unique(db, season_and_session):
    _, net_session = season_and_session
    log1 = ReminderLog(
        session_id=net_session.id,
        status=ReminderStatus.DRAFT,
        content_subject="s1",
        content_body="b1",
        drafted_at=datetime.now(timezone.utc),
    )
    log2 = ReminderLog(
        session_id=net_session.id,
        status=ReminderStatus.DRAFT,
        content_subject="s2",
        content_body="b2",
        drafted_at=datetime.now(timezone.utc),
    )
    db.add(log1)
    db.commit()
    db.add(log2)
    with pytest.raises(Exception):
        db.commit()


def test_reminder_log_relationships(db, season_and_session):
    _, net_session = season_and_session
    template = ReminderTemplate(
        name="Rel Template",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s",
        body_template="b",
    )
    db.add(template)
    db.flush()

    log = ReminderLog(
        session_id=net_session.id,
        template_id=template.id,
        status=ReminderStatus.DRAFT,
        content_subject="s",
        content_body="b",
        drafted_at=datetime.now(timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    assert log.session.id == net_session.id
    assert log.template.id == template.id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_reminder_models.py -v"`

Expected: FAIL — ImportError (module doesn't exist)

- [ ] **Step 3: Implement models**

`backend/modules/reminders/__init__.py`: empty file.

`backend/modules/reminders/models.py`:

```python
import enum
from datetime import datetime

from sqlalchemy import (
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class TemplateType(str, enum.Enum):
    REGULAR_CHECKIN = "regular_checkin"
    ACTIVITY = "activity"


class ReminderStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    SKIPPED = "skipped"


class ReminderTemplate(Base):
    __tablename__ = "reminder_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    template_type: Mapped[TemplateType] = mapped_column(
        Enum(TemplateType), nullable=False
    )
    subject_template: Mapped[str] = mapped_column(Text, nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ReminderLog(Base):
    __tablename__ = "reminder_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("net_sessions.id"), nullable=False, unique=True
    )
    template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reminder_templates.id"), nullable=True
    )
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus), nullable=False, default=ReminderStatus.DRAFT
    )
    content_subject: Mapped[str] = mapped_column(Text, nullable=False)
    content_body: Mapped[str] = mapped_column(Text, nullable=False)
    drafted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(String(20), nullable=True)

    session: Mapped["NetSession"] = relationship()
    template: Mapped["ReminderTemplate | None"] = relationship()
```

Note: The `relationship()` for `session` uses a forward reference string. SQLAlchemy resolves `"NetSession"` from the mapper registry since `backend.modules.schedule.models` is imported in tests (and in `alembic/env.py`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_reminder_models.py -v"`

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/reminders/__init__.py backend/modules/reminders/models.py tests/test_reminder_models.py
git commit -m "feat: add ReminderTemplate and ReminderLog models"
```

---

### Task 2: Reminder Service — Template CRUD and Rendering

**Files:**
- Create: `backend/modules/reminders/service.py`
- Create: `tests/test_reminder_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_reminder_service.py`:

```python
import pytest
from datetime import date, time, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
import backend.modules.schedule.models
import backend.modules.activities.models
import backend.modules.reminders.models
from backend.modules.reminders.models import (
    ReminderTemplate,
    ReminderLog,
    TemplateType,
    ReminderStatus,
)
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)
from backend.modules.activities.models import Activity
from backend.modules.reminders.service import (
    create_template,
    get_template,
    list_templates,
    update_template,
    delete_template,
    build_template_context,
    render_reminder,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def season_and_sessions(db):
    """Create a season with two sessions: regular check-in on 4/10, activity on 4/17."""
    season = NetSeason(
        name="Test Season",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 6, 30),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season)
    db.flush()

    session1 = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
        net_control_callsign="W0NE",
    )
    session2 = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 17),
        end_date=date(2026, 4, 17),
        grace_period_hours=24.0,
        session_type=SessionType.ACTIVITY,
        net_control_callsign="W0NC",
        activity_id=None,  # will be set below
    )
    db.add_all([session1, session2])
    db.flush()

    activity = Activity(
        title="Simplex Exercise",
        description="Practice simplex communications",
        instructions="Tune to 146.520 MHz and call CQ.",
    )
    db.add(activity)
    db.flush()
    session2.activity_id = activity.id
    db.commit()
    return season, session1, session2, activity


# --- Template CRUD Tests ---


def test_create_template(db):
    template = create_template(
        db,
        name="Regular Reminder",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net Reminder — {{ date }}",
        body_template="Check in on {{ date }}.",
        lead_time_days=2,
        is_default=True,
    )
    assert template.id is not None
    assert template.name == "Regular Reminder"
    assert template.is_default is True


def test_create_default_template_clears_previous(db):
    t1 = create_template(
        db,
        name="Old Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s",
        body_template="b",
        is_default=True,
    )
    t2 = create_template(
        db,
        name="New Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s",
        body_template="b",
        is_default=True,
    )
    db.refresh(t1)
    assert t1.is_default is False
    assert t2.is_default is True


def test_list_templates(db):
    create_template(db, name="T1", template_type=TemplateType.REGULAR_CHECKIN,
                    subject_template="s", body_template="b")
    create_template(db, name="T2", template_type=TemplateType.ACTIVITY,
                    subject_template="s", body_template="b")
    templates = list_templates(db)
    assert len(templates) == 2


def test_get_template(db):
    created = create_template(
        db, name="Find Me", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b",
    )
    found = get_template(db, created.id)
    assert found is not None
    assert found.name == "Find Me"


def test_update_template(db):
    template = create_template(
        db, name="Old Name", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="old", body_template="old",
    )
    updated = update_template(db, template.id, name="New Name", lead_time_days=5)
    assert updated is not None
    assert updated.name == "New Name"
    assert updated.lead_time_days == 5
    assert updated.subject_template == "old"  # not changed


def test_update_template_sets_default(db):
    t1 = create_template(
        db, name="First", template_type=TemplateType.ACTIVITY,
        subject_template="s", body_template="b", is_default=True,
    )
    t2 = create_template(
        db, name="Second", template_type=TemplateType.ACTIVITY,
        subject_template="s", body_template="b",
    )
    update_template(db, t2.id, is_default=True)
    db.refresh(t1)
    assert t1.is_default is False
    assert t2.is_default is True


def test_delete_template(db):
    template = create_template(
        db, name="Delete Me", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b",
    )
    assert delete_template(db, template.id) is True
    assert get_template(db, template.id) is None


def test_cannot_delete_default_template(db):
    template = create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    assert delete_template(db, template.id) is False
    assert get_template(db, template.id) is not None


# --- Template Rendering Tests ---


def test_build_template_context_regular(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    context = build_template_context(db, session1)
    assert context["date"] == "April 10, 2026"
    assert context["time"] == "6:00 PM"
    assert context["day_of_week"] == "Thursday"
    assert context["net_control"] == "W0NE"
    assert context["activity_title"] == ""
    assert context["activity_instructions"] == ""
    # Next week is session2 which is an activity week
    assert "Simplex Exercise" in context["next_week_preview"]


def test_build_template_context_activity(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    context = build_template_context(db, session2)
    assert context["date"] == "April 17, 2026"
    assert context["activity_title"] == "Simplex Exercise"
    assert "146.520" in context["activity_instructions"]
    assert context["net_control"] == "W0NC"


def test_build_template_context_no_next_session(db):
    """A session with no subsequent session should have empty next_week_preview."""
    season = NetSeason(
        name="Solo Season",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season)
    db.flush()
    session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
        net_control_callsign="W0NE",
    )
    db.add(session)
    db.commit()
    context = build_template_context(db, session)
    assert context["next_week_preview"] == ""


def test_render_reminder(db):
    template = ReminderTemplate(
        name="Test",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Reminder — {{ date }}",
        body_template="Check in on {{ date }}. Net control: {{ net_control }}",
    )
    context = {"date": "April 10, 2026", "net_control": "W0NE"}
    subject, body = render_reminder(template, context)
    assert subject == "Reminder — April 10, 2026"
    assert "W0NE" in body


def test_render_reminder_with_jinja2_conditional(db):
    template = ReminderTemplate(
        name="Conditional",
        template_type=TemplateType.ACTIVITY,
        subject_template="{{ activity_title }} — {{ date }}",
        body_template="{% if next_week_preview %}Next: {{ next_week_preview }}{% endif %}",
    )
    context = {
        "date": "April 17, 2026",
        "activity_title": "Simplex",
        "next_week_preview": "Standard Check-in",
    }
    subject, body = render_reminder(template, context)
    assert subject == "Simplex — April 17, 2026"
    assert body == "Next: Standard Check-in"


def test_render_reminder_bad_syntax(db):
    template = ReminderTemplate(
        name="Bad",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="OK subject",
        body_template="{% if broken %}",
    )
    context = {}
    subject, body = render_reminder(template, context)
    assert subject == "OK subject"
    assert "error" in body.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_reminder_service.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement service (template CRUD and rendering)**

`backend/modules/reminders/service.py`:

```python
from datetime import datetime, timezone

import jinja2
from sqlalchemy.orm import Session

from backend.modules.activities.models import Activity
from backend.modules.reminders.models import (
    ReminderLog,
    ReminderStatus,
    ReminderTemplate,
    TemplateType,
)
from backend.modules.schedule.models import (
    NetSession,
    SessionStatus,
    SessionType,
)


# --- Template CRUD ---


def create_template(
    db: Session,
    name: str,
    template_type: TemplateType,
    subject_template: str,
    body_template: str,
    lead_time_days: int = 2,
    is_default: bool = False,
) -> ReminderTemplate:
    if is_default:
        _clear_default(db, template_type)

    template = ReminderTemplate(
        name=name,
        template_type=template_type,
        subject_template=subject_template,
        body_template=body_template,
        lead_time_days=lead_time_days,
        is_default=is_default,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def get_template(db: Session, template_id: int) -> ReminderTemplate | None:
    return db.get(ReminderTemplate, template_id)


def list_templates(db: Session) -> list[ReminderTemplate]:
    return db.query(ReminderTemplate).order_by(ReminderTemplate.name).all()


def update_template(
    db: Session,
    template_id: int,
    name: str | None = None,
    template_type: TemplateType | None = None,
    subject_template: str | None = None,
    body_template: str | None = None,
    lead_time_days: int | None = None,
    is_default: bool | None = None,
) -> ReminderTemplate | None:
    template = db.get(ReminderTemplate, template_id)
    if template is None:
        return None

    if is_default is True:
        effective_type = template_type if template_type is not None else template.template_type
        _clear_default(db, effective_type)

    if name is not None:
        template.name = name
    if template_type is not None:
        template.template_type = template_type
    if subject_template is not None:
        template.subject_template = subject_template
    if body_template is not None:
        template.body_template = body_template
    if lead_time_days is not None:
        template.lead_time_days = lead_time_days
    if is_default is not None:
        template.is_default = is_default

    db.commit()
    db.refresh(template)
    return template


def delete_template(db: Session, template_id: int) -> bool:
    template = db.get(ReminderTemplate, template_id)
    if template is None:
        return False
    if template.is_default:
        return False
    db.delete(template)
    db.commit()
    return True


def _clear_default(db: Session, template_type: TemplateType) -> None:
    """Clear is_default on all templates of the given type."""
    db.query(ReminderTemplate).filter(
        ReminderTemplate.template_type == template_type,
        ReminderTemplate.is_default == True,
    ).update({"is_default": False})
    db.flush()


# --- Template Rendering ---


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def build_template_context(db: Session, net_session: NetSession) -> dict:
    """Build the Jinja2 context dict from session, season, activity, and next session."""
    season = net_session.season

    # Format date
    date_str = net_session.start_date.strftime("%B %d, %Y")
    # Remove leading zero from day: "April 09" -> "April 9"
    # strftime doesn't have a portable no-pad option, so we handle it
    parts = date_str.split(" ")
    if len(parts) >= 2 and parts[1].startswith("0"):
        parts[1] = parts[1].lstrip("0") + ","
        date_str = " ".join(parts)

    # Format time
    time_str = ""
    if season.time is not None:
        hour = season.time.hour
        minute = season.time.minute
        ampm = "AM" if hour < 12 else "PM"
        display_hour = hour % 12
        if display_hour == 0:
            display_hour = 12
        time_str = f"{display_hour}:{minute:02d} {ampm}"

    # Day of week
    day_of_week = ""
    if season.day_of_week is not None:
        day_of_week = _DAY_NAMES[season.day_of_week]

    # Activity info
    activity_title = ""
    activity_instructions = ""
    if net_session.activity_id is not None:
        activity = db.get(Activity, net_session.activity_id)
        if activity is not None:
            activity_title = activity.title
            activity_instructions = activity.instructions

    # Net control
    net_control = net_session.net_control_callsign or ""

    # Next week preview
    next_week_preview = _build_next_week_preview(db, net_session)

    return {
        "date": date_str,
        "time": time_str,
        "day_of_week": day_of_week,
        "activity_title": activity_title,
        "activity_instructions": activity_instructions,
        "net_control": net_control,
        "next_week_preview": next_week_preview,
    }


def _build_next_week_preview(db: Session, current_session: NetSession) -> str:
    """Look up the next session after current_session in the same season."""
    next_session = (
        db.query(NetSession)
        .filter(
            NetSession.season_id == current_session.season_id,
            NetSession.start_date > current_session.start_date,
            NetSession.status != SessionStatus.CANCELLED,
        )
        .order_by(NetSession.start_date)
        .first()
    )
    if next_session is None:
        return ""

    if next_session.session_type == SessionType.ACTIVITY and next_session.activity_id:
        activity = db.get(Activity, next_session.activity_id)
        if activity:
            return f"{activity.title} — {activity.description}"
    return "Standard Winlink Check-in"


def render_reminder(
    template: ReminderTemplate, context: dict
) -> tuple[str, str]:
    """Render subject and body templates with Jinja2. Returns (subject, body).

    If the body template has a syntax error, the subject is rendered normally
    and the body contains an error message.
    """
    env = jinja2.Environment(undefined=jinja2.Undefined)

    try:
        subject = env.from_string(template.subject_template).render(context)
    except jinja2.TemplateError:
        subject = template.subject_template

    try:
        body = env.from_string(template.body_template).render(context)
    except jinja2.TemplateError as e:
        body = f"[Template rendering error: {e}]"

    return subject, body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_reminder_service.py -v"`

Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/reminders/service.py tests/test_reminder_service.py
git commit -m "feat: add reminder service with template CRUD and Jinja2 rendering"
```

---

### Task 3: Reminder Service — Draft Generation and Status Transitions

**Files:**
- Modify: `backend/modules/reminders/service.py`
- Modify: `tests/test_reminder_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reminder_service.py`:

```python
from unittest.mock import patch
from backend.modules.reminders.service import (
    generate_draft,
    generate_due_drafts,
    approve_reminder,
    mark_sent,
    skip_reminder,
    update_draft,
)


# --- Draft Generation Tests ---


def test_generate_draft(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    template = create_template(
        db, name="Default Regular", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Reminder — {{ date }}", body_template="Check in on {{ date }}.",
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None
    assert log.status == ReminderStatus.DRAFT
    assert log.template_id == template.id
    assert "April 10, 2026" in log.content_subject
    assert log.drafted_at is not None


def test_generate_draft_is_idempotent(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default Regular", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    log1 = generate_draft(db, session1.id)
    log2 = generate_draft(db, session1.id)
    assert log1.id == log2.id  # same record returned


def test_generate_draft_with_explicit_template(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="default", body_template="default", is_default=True,
    )
    custom = create_template(
        db, name="Custom", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="custom — {{ date }}", body_template="custom body",
    )
    log = generate_draft(db, session1.id, template_id=custom.id)
    assert log.template_id == custom.id
    assert "custom" in log.content_subject


def test_generate_due_drafts(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default Regular", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="regular — {{ date }}", body_template="b",
        is_default=True, lead_time_days=3,
    )
    create_template(
        db, name="Default Activity", template_type=TemplateType.ACTIVITY,
        subject_template="activity — {{ date }}", body_template="b",
        is_default=True, lead_time_days=3,
    )
    # Simulate "today" is April 8 — session1 is April 10 (2 days away, within 3-day lead)
    # session2 is April 17 (9 days away, outside 3-day lead)
    with patch("backend.modules.reminders.service._today", return_value=date(2026, 4, 8)):
        drafts = generate_due_drafts(db)
    assert len(drafts) == 1
    assert drafts[0].session_id == session1.id


def test_generate_due_drafts_skips_completed_sessions(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    session1.status = SessionStatus.COMPLETED
    db.commit()
    create_template(
        db, name="Default Regular", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True, lead_time_days=30,
    )
    create_template(
        db, name="Default Activity", template_type=TemplateType.ACTIVITY,
        subject_template="s", body_template="b", is_default=True, lead_time_days=30,
    )
    with patch("backend.modules.reminders.service._today", return_value=date(2026, 4, 8)):
        drafts = generate_due_drafts(db)
    # session1 is COMPLETED, session2 is still SCHEDULED but within lead time
    assert all(d.session_id != session1.id for d in drafts)


# --- Status Transition Tests ---


def test_approve_reminder(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    log = generate_draft(db, session1.id)
    approved = approve_reminder(db, log.id, "W0NE")
    assert approved is not None
    assert approved.status == ReminderStatus.APPROVED
    assert approved.approved_by == "W0NE"
    assert approved.approved_at is not None


def test_approve_non_draft_returns_none(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    log = generate_draft(db, session1.id)
    approve_reminder(db, log.id, "W0NE")
    # Try to approve again — already approved
    result = approve_reminder(db, log.id, "W0NE")
    assert result is None


def test_mark_sent(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    log = generate_draft(db, session1.id)
    approve_reminder(db, log.id, "W0NE")
    sent = mark_sent(db, log.id)
    assert sent is not None
    assert sent.status == ReminderStatus.SENT
    assert sent.sent_at is not None


def test_mark_sent_non_approved_returns_none(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    log = generate_draft(db, session1.id)
    result = mark_sent(db, log.id)  # still draft
    assert result is None


def test_skip_reminder_from_draft(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    log = generate_draft(db, session1.id)
    skipped = skip_reminder(db, log.id)
    assert skipped is not None
    assert skipped.status == ReminderStatus.SKIPPED


def test_skip_reminder_from_approved(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    log = generate_draft(db, session1.id)
    approve_reminder(db, log.id, "W0NE")
    skipped = skip_reminder(db, log.id)
    assert skipped is not None
    assert skipped.status == ReminderStatus.SKIPPED


def test_update_draft(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    log = generate_draft(db, session1.id)
    updated = update_draft(db, log.id, content_subject="Edited subject", content_body="Edited body")
    assert updated is not None
    assert updated.content_subject == "Edited subject"
    assert updated.content_body == "Edited body"


def test_update_draft_non_draft_returns_none(db, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db, name="Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="s", body_template="b", is_default=True,
    )
    log = generate_draft(db, session1.id)
    approve_reminder(db, log.id, "W0NE")
    result = update_draft(db, log.id, content_subject="No")
    assert result is None
```

- [ ] **Step 2: Run tests to verify the new tests fail**

Run: `nix-shell --run "pytest tests/test_reminder_service.py -v"`

Expected: Previous 15 pass, new tests FAIL — ImportError for `generate_draft` etc.

- [ ] **Step 3: Implement draft generation and status transitions**

Append to `backend/modules/reminders/service.py`:

```python
from datetime import date as date_type


def _today() -> date_type:
    """Return today's date. Extracted for test mocking."""
    return date_type.today()


# --- Draft Generation ---


def generate_draft(
    db: Session,
    session_id: int,
    template_id: int | None = None,
) -> ReminderLog | None:
    """Generate a reminder draft for a session. Idempotent — returns existing if present."""
    existing = (
        db.query(ReminderLog)
        .filter(ReminderLog.session_id == session_id)
        .first()
    )
    if existing is not None:
        return existing

    net_session = db.get(NetSession, session_id)
    if net_session is None:
        return None

    # Pick template
    if template_id is not None:
        template = db.get(ReminderTemplate, template_id)
    else:
        session_template_type = _session_type_to_template_type(net_session.session_type)
        template = (
            db.query(ReminderTemplate)
            .filter(
                ReminderTemplate.template_type == session_template_type,
                ReminderTemplate.is_default == True,
            )
            .first()
        )

    if template is None:
        return None

    context = build_template_context(db, net_session)
    subject, body = render_reminder(template, context)

    log = ReminderLog(
        session_id=session_id,
        template_id=template.id,
        status=ReminderStatus.DRAFT,
        content_subject=subject,
        content_body=body,
        drafted_at=datetime.now(timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def generate_due_drafts(db: Session) -> list[ReminderLog]:
    """Generate drafts for all scheduled sessions that are due based on lead time."""
    today = _today()
    drafts: list[ReminderLog] = []

    # Get default templates to know lead times
    default_templates = (
        db.query(ReminderTemplate)
        .filter(ReminderTemplate.is_default == True)
        .all()
    )
    lead_times = {t.template_type: t.lead_time_days for t in default_templates}

    # Find scheduled sessions without existing reminders
    sessions = (
        db.query(NetSession)
        .filter(
            NetSession.status == SessionStatus.SCHEDULED,
            ~NetSession.id.in_(
                db.query(ReminderLog.session_id)
            ),
        )
        .all()
    )

    for net_session in sessions:
        ttype = _session_type_to_template_type(net_session.session_type)
        lead = lead_times.get(ttype)
        if lead is None:
            continue  # no default template for this type

        days_until = (net_session.start_date - today).days
        if days_until <= lead:
            draft = generate_draft(db, net_session.id)
            if draft is not None:
                drafts.append(draft)

    return drafts


def _session_type_to_template_type(session_type: SessionType) -> TemplateType:
    if session_type == SessionType.ACTIVITY:
        return TemplateType.ACTIVITY
    return TemplateType.REGULAR_CHECKIN


# --- Status Transitions ---


def approve_reminder(
    db: Session, reminder_id: int, approver_callsign: str
) -> ReminderLog | None:
    """Approve a draft reminder. Returns None if not in draft status."""
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status != ReminderStatus.DRAFT:
        return None

    log.status = ReminderStatus.APPROVED
    log.approved_at = datetime.now(timezone.utc)
    log.approved_by = approver_callsign
    db.commit()
    db.refresh(log)
    return log


def mark_sent(db: Session, reminder_id: int) -> ReminderLog | None:
    """Mark an approved reminder as sent. Returns None if not approved."""
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status != ReminderStatus.APPROVED:
        return None

    log.status = ReminderStatus.SENT
    log.sent_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(log)
    return log


def skip_reminder(db: Session, reminder_id: int) -> ReminderLog | None:
    """Skip a reminder. Valid from draft or approved status."""
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status not in (ReminderStatus.DRAFT, ReminderStatus.APPROVED):
        return None

    log.status = ReminderStatus.SKIPPED
    db.commit()
    db.refresh(log)
    return log


def update_draft(
    db: Session,
    reminder_id: int,
    content_subject: str | None = None,
    content_body: str | None = None,
) -> ReminderLog | None:
    """Edit draft content. Only valid while status is draft."""
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status != ReminderStatus.DRAFT:
        return None

    if content_subject is not None:
        log.content_subject = content_subject
    if content_body is not None:
        log.content_body = content_body

    db.commit()
    db.refresh(log)
    return log
```

Also add the missing import at the top of the file — update the `from datetime import` line:

```python
from datetime import date as date_type, datetime, timezone
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_reminder_service.py -v"`

Expected: 28 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/reminders/service.py tests/test_reminder_service.py
git commit -m "feat: add draft generation and status transitions to reminder service"
```

---

### Task 4: Reminder API Routes

**Files:**
- Create: `backend/modules/reminders/routes.py`
- Create: `tests/test_reminder_routes.py`

- [ ] **Step 1: Write the failing test**

`tests/test_reminder_routes.py`:

```python
import pytest
from datetime import date, datetime, timezone
from unittest.mock import patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)
from backend.modules.activities.models import Activity
from backend.modules.reminders.models import (
    ReminderTemplate,
    ReminderLog,
    TemplateType,
    ReminderStatus,
)
from backend.modules.reminders.routes import reminders_router
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
        nc = User(
            callsign="W0NC",
            oidc_subject="auth0|nc",
            name="Net Control",
            role=UserRole.NET_CONTROL,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, nc, viewer])

        # Season + session
        season = NetSeason(
            name="Test Season",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            day_of_week=3,
        )
        session.add(season)
        session.flush()

        net_session = NetSession(
            season_id=season.id,
            start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
            net_control_callsign="W0NE",
        )
        session.add(net_session)
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(reminders_router, prefix="/api/reminders")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Template CRUD Routes ---


@pytest.mark.asyncio
async def test_create_template(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/reminders/templates",
        json={
            "name": "Regular Reminder",
            "template_type": "regular_checkin",
            "subject_template": "Net Reminder — {{ date }}",
            "body_template": "Check in on {{ date }}.",
            "lead_time_days": 2,
            "is_default": True,
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Regular Reminder"
    assert data["is_default"] is True


@pytest.mark.asyncio
async def test_list_templates(test_client, test_settings, db_setup):
    with db_setup() as session:
        session.add(ReminderTemplate(
            name="T1", template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="s", body_template="b",
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/reminders/templates",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_update_template(test_client, test_settings, db_setup):
    with db_setup() as session:
        t = ReminderTemplate(
            name="Old", template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="s", body_template="b",
        )
        session.add(t)
        session.commit()
        tid = t.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.patch(
        f"/api/reminders/templates/{tid}",
        json={"name": "New Name"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_delete_template(test_client, test_settings, db_setup):
    with db_setup() as session:
        t = ReminderTemplate(
            name="Delete Me", template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="s", body_template="b", is_default=False,
        )
        session.add(t)
        session.commit()
        tid = t.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.delete(
        f"/api/reminders/templates/{tid}",
        cookies={"access_token": token},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_cannot_delete_default_template(test_client, test_settings, db_setup):
    with db_setup() as session:
        t = ReminderTemplate(
            name="Default", template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="s", body_template="b", is_default=True,
        )
        session.add(t)
        session.commit()
        tid = t.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.delete(
        f"/api/reminders/templates/{tid}",
        cookies={"access_token": token},
    )
    assert response.status_code == 400


# --- Draft Generation Routes ---


@pytest.mark.asyncio
async def test_generate_draft_for_session(test_client, test_settings, db_setup):
    with db_setup() as session:
        session.add(ReminderTemplate(
            name="Default Regular", template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Reminder — {{ date }}",
            body_template="Check in on {{ date }}.",
            is_default=True,
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/reminders/generate/1",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert "April 10, 2026" in data["content_subject"]


@pytest.mark.asyncio
async def test_generate_due_drafts(test_client, test_settings, db_setup):
    with db_setup() as session:
        session.add(ReminderTemplate(
            name="Default Regular", template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="s", body_template="b",
            is_default=True, lead_time_days=5,
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    with patch("backend.modules.reminders.service._today", return_value=date(2026, 4, 8)):
        response = await test_client.post(
            "/api/reminders/generate",
            cookies={"access_token": token},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["generated"] >= 1


# --- Reminder Management Routes ---


@pytest.mark.asyncio
async def test_get_reminder_for_session(test_client, test_settings, db_setup):
    with db_setup() as session:
        session.add(ReminderTemplate(
            name="Default Regular", template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="s", body_template="b", is_default=True,
        ))
        session.add(ReminderLog(
            session_id=1, status=ReminderStatus.DRAFT,
            content_subject="Test Subject", content_body="Test Body",
            drafted_at=datetime.now(timezone.utc),
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/reminders/session/1",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["content_subject"] == "Test Subject"


@pytest.mark.asyncio
async def test_approve_reminder(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1, status=ReminderStatus.DRAFT,
            content_subject="s", content_body="b",
            drafted_at=datetime.now(timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NC", "net_control", test_settings)
    response = await test_client.post(
        f"/api/reminders/{log_id}/approve",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["approved_by"] == "W0NC"


@pytest.mark.asyncio
async def test_send_reminder(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1, status=ReminderStatus.APPROVED,
            content_subject="s", content_body="b",
            drafted_at=datetime.now(timezone.utc),
            approved_at=datetime.now(timezone.utc),
            approved_by="W0NE",
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        f"/api/reminders/{log_id}/send",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "sent"


@pytest.mark.asyncio
async def test_skip_reminder(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1, status=ReminderStatus.DRAFT,
            content_subject="s", content_body="b",
            drafted_at=datetime.now(timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NC", "net_control", test_settings)
    response = await test_client.post(
        f"/api/reminders/{log_id}/skip",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"


@pytest.mark.asyncio
async def test_edit_draft(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1, status=ReminderStatus.DRAFT,
            content_subject="old", content_body="old",
            drafted_at=datetime.now(timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NC", "net_control", test_settings)
    response = await test_client.patch(
        f"/api/reminders/{log_id}",
        json={"content_subject": "new subject", "content_body": "new body"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["content_subject"] == "new subject"


@pytest.mark.asyncio
async def test_list_reminders_with_status_filter(test_client, test_settings, db_setup):
    with db_setup() as session:
        session.add(ReminderLog(
            session_id=1, status=ReminderStatus.DRAFT,
            content_subject="s", content_body="b",
            drafted_at=datetime.now(timezone.utc),
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/reminders/?status=draft",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = await test_client.get(
        "/api/reminders/?status=sent",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert len(response.json()) == 0


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_generate(test_client, test_settings, db_setup):
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)

    # Viewer can list reminders
    response = await test_client.get(
        "/api/reminders/",
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 200

    # Viewer cannot generate
    response = await test_client.post(
        "/api/reminders/generate/1",
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_reminder_routes.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement routes**

`backend/modules/reminders/routes.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.modules.reminders.models import (
    ReminderLog,
    ReminderStatus,
    ReminderTemplate,
    TemplateType,
)
from backend.modules.reminders.service import (
    approve_reminder,
    create_template,
    delete_template,
    generate_draft,
    generate_due_drafts,
    get_template,
    list_templates,
    mark_sent,
    skip_reminder,
    update_draft,
    update_template,
)

reminders_router = APIRouter(tags=["reminders"])


# --- Pydantic schemas ---


class TemplateCreate(BaseModel):
    name: str
    template_type: TemplateType
    subject_template: str
    body_template: str
    lead_time_days: int = 2
    is_default: bool = False


class TemplateUpdate(BaseModel):
    name: str | None = None
    template_type: TemplateType | None = None
    subject_template: str | None = None
    body_template: str | None = None
    lead_time_days: int | None = None
    is_default: bool | None = None


class DraftUpdate(BaseModel):
    content_subject: str | None = None
    content_body: str | None = None


# --- Helpers ---


def _template_to_response(template: ReminderTemplate) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "template_type": template.template_type.value,
        "subject_template": template.subject_template,
        "body_template": template.body_template,
        "lead_time_days": template.lead_time_days,
        "is_default": template.is_default,
    }


def _reminder_to_response(log: ReminderLog) -> dict:
    return {
        "id": log.id,
        "session_id": log.session_id,
        "template_id": log.template_id,
        "status": log.status.value,
        "content_subject": log.content_subject,
        "content_body": log.content_body,
        "drafted_at": log.drafted_at.isoformat(),
        "approved_at": log.approved_at.isoformat() if log.approved_at else None,
        "sent_at": log.sent_at.isoformat() if log.sent_at else None,
        "approved_by": log.approved_by,
    }


# --- Template Routes ---


@reminders_router.post("/templates", status_code=201)
async def create_template_route(
    body: TemplateCreate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    template = create_template(
        db,
        name=body.name,
        template_type=body.template_type,
        subject_template=body.subject_template,
        body_template=body.body_template,
        lead_time_days=body.lead_time_days,
        is_default=body.is_default,
    )
    return _template_to_response(template)


@reminders_router.get("/templates")
async def list_templates_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    templates = list_templates(db)
    return [_template_to_response(t) for t in templates]


@reminders_router.patch("/templates/{template_id}")
async def update_template_route(
    template_id: int,
    body: TemplateUpdate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    template = update_template(
        db,
        template_id,
        name=body.name,
        template_type=body.template_type,
        subject_template=body.subject_template,
        body_template=body.body_template,
        lead_time_days=body.lead_time_days,
        is_default=body.is_default,
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return _template_to_response(template)


@reminders_router.delete("/templates/{template_id}", status_code=204)
async def delete_template_route(
    template_id: int,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    template = get_template(db, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete a default template")
    delete_template(db, template_id)


# --- Draft Generation Routes ---


@reminders_router.post("/generate")
async def generate_due_drafts_route(
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    drafts = generate_due_drafts(db)
    return {
        "generated": len(drafts),
        "reminders": [_reminder_to_response(d) for d in drafts],
    }


@reminders_router.post("/generate/{session_id}")
async def generate_draft_route(
    session_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = generate_draft(db, session_id)
    if log is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found or no default template for session type",
        )
    return _reminder_to_response(log)


# --- Reminder Management Routes ---


@reminders_router.get("/")
async def list_reminders_route(
    status: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    query = db.query(ReminderLog).order_by(ReminderLog.drafted_at.desc())
    if status is not None:
        query = query.filter(ReminderLog.status == ReminderStatus(status))
    return [_reminder_to_response(log) for log in query.all()]


@reminders_router.get("/session/{session_id}")
async def get_session_reminder_route(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    log = db.query(ReminderLog).filter(ReminderLog.session_id == session_id).first()
    if log is None:
        raise HTTPException(status_code=404, detail="Reminder not found for this session")
    return _reminder_to_response(log)


@reminders_router.patch("/{reminder_id}")
async def update_draft_route(
    reminder_id: int,
    body: DraftUpdate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = update_draft(
        db, reminder_id,
        content_subject=body.content_subject,
        content_body=body.content_body,
    )
    if log is None:
        raise HTTPException(
            status_code=409,
            detail="Reminder not found or not in draft status",
        )
    return _reminder_to_response(log)


@reminders_router.post("/{reminder_id}/approve")
async def approve_reminder_route(
    reminder_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = approve_reminder(db, reminder_id, user.callsign)
    if log is None:
        raise HTTPException(
            status_code=409,
            detail="Reminder not found or not in draft status",
        )
    return _reminder_to_response(log)


@reminders_router.post("/{reminder_id}/send")
async def send_reminder_route(
    reminder_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = mark_sent(db, reminder_id)
    if log is None:
        raise HTTPException(
            status_code=409,
            detail="Reminder not found or not in approved status",
        )
    return _reminder_to_response(log)


@reminders_router.post("/{reminder_id}/skip")
async def skip_reminder_route(
    reminder_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = skip_reminder(db, reminder_id)
    if log is None:
        raise HTTPException(
            status_code=409,
            detail="Reminder not found or not in draft/approved status",
        )
    return _reminder_to_response(log)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_reminder_routes.py -v"`

Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/reminders/routes.py tests/test_reminder_routes.py
git commit -m "feat: add reminder API routes"
```

---

### Task 5: Alembic Migration

**Files:**
- Modify: `alembic/env.py`
- Auto-generate migration

- [ ] **Step 1: Add reminders model import to alembic/env.py**

Add after the existing model imports (after the `import backend.modules.checkins.models` line):

```python
import backend.modules.reminders.models  # noqa: F401
```

- [ ] **Step 2: Auto-generate the migration**

Run: `nix-shell --run "alembic revision --autogenerate -m 'add reminders tables'"`

Expected: Creates a migration file with `reminder_templates` and `reminder_logs` tables

- [ ] **Step 3: Verify the migration has the correct tables**

Read the generated migration file. It should create 2 tables:
- `reminder_templates` with columns: id, name (unique), template_type, subject_template, body_template, lead_time_days, is_default
- `reminder_logs` with columns: id, session_id (unique FK), template_id (FK nullable), status, content_subject, content_body, drafted_at, approved_at, sent_at, approved_by

- [ ] **Step 4: Run the migration**

Run: `nix-shell --run "alembic upgrade head"`

Expected: Migration applies successfully

- [ ] **Step 5: Seed default templates**

The migration should include seed data. If the auto-generated migration does not include it, manually add to the `upgrade()` function after the table creation:

```python
    op.execute(
        """
        INSERT INTO reminder_templates (name, template_type, subject_template, body_template, lead_time_days, is_default)
        VALUES (
            'Regular Check-in Reminder',
            'regular_checkin',
            'W0NE Winlink Net Reminder — {{ date }}',
            'Reminder: the W0NE Winlink Net check-in is this {{ day_of_week }}, {{ date }}.\n\nPlease send your check-in to w0ne@winlink.org with your name, callsign, city, county, state, and mode.\n\nNet control: {{ net_control }}\n{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}',
            2,
            1
        )
        """
    )
    op.execute(
        """
        INSERT INTO reminder_templates (name, template_type, subject_template, body_template, lead_time_days, is_default)
        VALUES (
            'Activity Week Reminder',
            'activity',
            'W0NE Winlink Net — {{ activity_title }} — {{ date }}',
            'This {{ day_of_week }}''s W0NE Winlink Net features a special activity: **{{ activity_title }}**\n\n{{ activity_instructions }}\n\nPlease send your check-in to w0ne@winlink.org with your name, callsign, city, county, state, and mode.\n\nNet control: {{ net_control }}\n{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}',
            2,
            1
        )
        """
    )
```

And add matching deletes to `downgrade()`:

```python
    op.execute("DELETE FROM reminder_templates WHERE name IN ('Regular Check-in Reminder', 'Activity Week Reminder')")
```

- [ ] **Step 6: Clean up test database**

Run: `rm -f skynetcontrol.db`

- [ ] **Step 7: Commit**

```bash
git add alembic/env.py alembic/versions/
git commit -m "feat: add migration for reminders tables with seed templates"
```

---

### Task 6: Wire Into app.py and Final Verification

**Files:**
- Modify: `backend/app.py`

- [ ] **Step 1: Register reminders router in app.py**

Add import after the existing router imports:

```python
from backend.modules.reminders.routes import reminders_router
```

Add router registration after the existing `include_router` calls:

```python
    app.include_router(reminders_router, prefix="/api/reminders")
```

- [ ] **Step 2: Run full test suite**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All tests pass (126 existing + new reminder tests)

- [ ] **Step 3: Verify Nix build**

Run: `nix-build default.nix`

Expected: Builds successfully (Jinja2 is already available via FastAPI/Starlette dependency)

- [ ] **Step 4: Clean up any test database files**

Run: `rm -f skynetcontrol.db`

- [ ] **Step 5: Commit**

```bash
git add backend/app.py
git commit -m "feat: wire reminders module into app"
```
