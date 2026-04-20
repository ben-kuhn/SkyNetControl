# Schedule Module Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-event session support, nullable season_id/end_date, ad-hoc session creation, and input validation to the existing schedule module.

**Architecture:** Incremental changes to the existing `backend/modules/schedule/` module — new enum value, schema changes, new service functions, new routes, and an Alembic migration. The existing season auto-generation flow is unchanged.

**Tech Stack:** FastAPI, SQLAlchemy 2.0+, Alembic, pytest, httpx

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/modules/schedule/models.py` | Modify | Add `REAL_EVENT` enum value, make `season_id` and `end_date` nullable |
| `backend/modules/schedule/service.py` | Modify | Add `create_session`, `get_session`, `list_sessions`, `update_session` functions |
| `backend/modules/schedule/routes.py` | Modify | Add `POST /sessions`, `GET /sessions`, `GET /sessions/{id}` routes; add validation to `POST /seasons` |
| `alembic/versions/*_update_schedule_for_real_events.py` | Create | Migration for enum + nullable changes |
| `tests/test_schedule_models.py` | Modify | Add tests for REAL_EVENT type, nullable season_id, nullable end_date |
| `tests/test_schedule_service.py` | Modify | Add tests for create_session, list_sessions, update_session |
| `tests/test_schedule_api.py` | Modify | Add tests for new routes and validation |

---

### Task 1: Model Changes — Add REAL_EVENT and Nullable Fields

**Files:**
- Modify: `backend/modules/schedule/models.py`
- Modify: `tests/test_schedule_models.py`

- [ ] **Step 1: Write failing test for REAL_EVENT session type**

Add to `tests/test_schedule_models.py`:

```python
def test_create_real_event_session_no_season(db: Session):
    session_obj = NetSession(
        season_id=None,
        start_date=date(2026, 4, 15),
        end_date=None,
        grace_period_hours=24,
        session_type=SessionType.REAL_EVENT,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(session_obj)
    db.commit()

    fetched = db.get(NetSession, session_obj.id)
    assert fetched is not None
    assert fetched.session_type == SessionType.REAL_EVENT
    assert fetched.season_id is None
    assert fetched.end_date is None
    assert fetched.season is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "pytest tests/test_schedule_models.py::test_create_real_event_session_no_season -v"`
Expected: FAIL — `REAL_EVENT` not a member of `SessionType`

- [ ] **Step 3: Update models to add REAL_EVENT and make fields nullable**

In `backend/modules/schedule/models.py`, make these changes:

1. Add `REAL_EVENT` to the `SessionType` enum:

```python
class SessionType(str, enum.Enum):
    REGULAR_CHECKIN = "regular_checkin"
    ACTIVITY = "activity"
    REAL_EVENT = "real_event"
```

2. Make `season_id` nullable on `NetSession`:

```python
    season_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("net_seasons.id"), nullable=True)
```

3. Make `end_date` nullable on `NetSession`:

```python
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
```

4. Make the `season` relationship nullable:

```python
    season: Mapped["NetSeason | None"] = relationship(back_populates="sessions")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix-shell --run "pytest tests/test_schedule_models.py::test_create_real_event_session_no_season -v"`
Expected: PASS

- [ ] **Step 5: Run all existing schedule model tests to check for regressions**

Run: `nix-shell --run "pytest tests/test_schedule_models.py -v"`
Expected: All PASS — existing tests create sessions with season_id and end_date, so they remain valid.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/schedule/models.py tests/test_schedule_models.py
git commit -m "feat(schedule): add REAL_EVENT type, make season_id and end_date nullable"
```

---

### Task 2: Service Layer — Ad-hoc Session CRUD

**Files:**
- Modify: `backend/modules/schedule/service.py`
- Modify: `tests/test_schedule_service.py`

- [ ] **Step 1: Write failing test for create_session**

Add to `tests/test_schedule_service.py`:

```python
from backend.modules.schedule.service import (
    generate_sessions,
    create_session,
    get_session,
    list_sessions,
    update_session,
)


def test_create_adhoc_session(db: Session):
    session_obj = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REGULAR_CHECKIN,
        net_control_callsign="W0NE",
    )
    assert session_obj.id is not None
    assert session_obj.season_id is None
    assert session_obj.end_date is None
    assert session_obj.session_type == SessionType.REGULAR_CHECKIN
    assert session_obj.status == SessionStatus.SCHEDULED
    assert session_obj.grace_period_hours == 24.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "pytest tests/test_schedule_service.py::test_create_adhoc_session -v"`
Expected: FAIL — `create_session` not importable

- [ ] **Step 3: Implement create_session**

Add to `backend/modules/schedule/service.py`:

```python
def create_session(
    db: Session,
    start_date: date,
    session_type: SessionType,
    end_date: date | None = None,
    season_id: int | None = None,
    grace_period_hours: float = 24.0,
    net_control_callsign: str | None = None,
    activity_id: int | None = None,
) -> NetSession:
    session_obj = NetSession(
        season_id=season_id,
        start_date=start_date,
        end_date=end_date,
        grace_period_hours=grace_period_hours,
        session_type=session_type,
        status=SessionStatus.SCHEDULED,
        net_control_callsign=net_control_callsign,
        activity_id=activity_id,
    )
    db.add(session_obj)
    db.commit()
    db.refresh(session_obj)
    return session_obj
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix-shell --run "pytest tests/test_schedule_service.py::test_create_adhoc_session -v"`
Expected: PASS

- [ ] **Step 5: Write failing test for create_session with real event**

Add to `tests/test_schedule_service.py`:

```python
def test_create_real_event_session(db: Session):
    session_obj = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
        net_control_callsign="W0NE",
    )
    assert session_obj.session_type == SessionType.REAL_EVENT
    assert session_obj.season_id is None
    assert session_obj.end_date is None
```

- [ ] **Step 6: Run test to verify it passes (already implemented)**

Run: `nix-shell --run "pytest tests/test_schedule_service.py::test_create_real_event_session -v"`
Expected: PASS

- [ ] **Step 7: Write failing test for get_session**

Add to `tests/test_schedule_service.py`:

```python
def test_get_session(db: Session):
    created = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REGULAR_CHECKIN,
    )
    fetched = get_session(db, created.id)
    assert fetched is not None
    assert fetched.id == created.id

    missing = get_session(db, 9999)
    assert missing is None
```

- [ ] **Step 8: Run test to verify it fails**

Run: `nix-shell --run "pytest tests/test_schedule_service.py::test_get_session -v"`
Expected: FAIL — `get_session` not defined

- [ ] **Step 9: Implement get_session**

Add to `backend/modules/schedule/service.py`:

```python
def get_session(db: Session, session_id: int) -> NetSession | None:
    return db.get(NetSession, session_id)
```

- [ ] **Step 10: Run test to verify it passes**

Run: `nix-shell --run "pytest tests/test_schedule_service.py::test_get_session -v"`
Expected: PASS

- [ ] **Step 11: Write failing test for list_sessions**

Add to `tests/test_schedule_service.py`:

```python
def test_list_sessions_no_filter(db: Session):
    season = NetSeason(
        name="Test",
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 10),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()
    generate_sessions(db, season, default_net_control="W0NE")

    # Also create an ad-hoc real event
    create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
    )

    all_sessions = list_sessions(db)
    assert len(all_sessions) == 3  # 2 from season + 1 ad-hoc


def test_list_sessions_filter_by_season(db: Session):
    season = NetSeason(
        name="Test",
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 10),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()
    generate_sessions(db, season, default_net_control="W0NE")

    create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
    )

    season_sessions = list_sessions(db, season_id=season.id)
    assert len(season_sessions) == 2
    for s in season_sessions:
        assert s.season_id == season.id


def test_list_sessions_filter_by_status(db: Session):
    s1 = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
    )
    create_session(
        db,
        start_date=date(2026, 4, 16),
        session_type=SessionType.REGULAR_CHECKIN,
    )

    # Cancel the first one
    s1.status = SessionStatus.CANCELLED
    db.commit()

    cancelled = list_sessions(db, status=SessionStatus.CANCELLED)
    assert len(cancelled) == 1
    assert cancelled[0].id == s1.id
```

- [ ] **Step 12: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_schedule_service.py::test_list_sessions_no_filter tests/test_schedule_service.py::test_list_sessions_filter_by_season tests/test_schedule_service.py::test_list_sessions_filter_by_status -v"`
Expected: FAIL — `list_sessions` not defined

- [ ] **Step 13: Implement list_sessions**

Add to `backend/modules/schedule/service.py`:

```python
def list_sessions(
    db: Session,
    season_id: int | None = None,
    status: SessionStatus | None = None,
) -> list[NetSession]:
    query = db.query(NetSession)
    if season_id is not None:
        query = query.filter(NetSession.season_id == season_id)
    if status is not None:
        query = query.filter(NetSession.status == status)
    return query.order_by(NetSession.start_date).all()
```

- [ ] **Step 14: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_schedule_service.py::test_list_sessions_no_filter tests/test_schedule_service.py::test_list_sessions_filter_by_season tests/test_schedule_service.py::test_list_sessions_filter_by_status -v"`
Expected: All PASS

- [ ] **Step 15: Write failing test for update_session**

Add to `tests/test_schedule_service.py`:

```python
def test_update_session(db: Session):
    session_obj = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
    )

    updated = update_session(
        db,
        session_obj.id,
        status=SessionStatus.COMPLETED,
        end_date=date(2026, 4, 17),
        net_control_callsign="W0NE",
    )
    assert updated is not None
    assert updated.status == SessionStatus.COMPLETED
    assert updated.end_date == date(2026, 4, 17)
    assert updated.net_control_callsign == "W0NE"


def test_update_session_not_found(db: Session):
    result = update_session(db, 9999, status=SessionStatus.COMPLETED)
    assert result is None
```

- [ ] **Step 16: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_schedule_service.py::test_update_session tests/test_schedule_service.py::test_update_session_not_found -v"`
Expected: FAIL — `update_session` not defined

- [ ] **Step 17: Implement update_session**

Add to `backend/modules/schedule/service.py`:

```python
def update_session(
    db: Session,
    session_id: int,
    status: SessionStatus | None = None,
    session_type: SessionType | None = None,
    net_control_callsign: str | None = None,
    activity_id: int | None = None,
    grace_period_hours: float | None = None,
    end_date: date | None = None,
) -> NetSession | None:
    session_obj = db.get(NetSession, session_id)
    if session_obj is None:
        return None

    if status is not None:
        session_obj.status = status
    if session_type is not None:
        session_obj.session_type = session_type
    if net_control_callsign is not None:
        session_obj.net_control_callsign = net_control_callsign
    if activity_id is not None:
        session_obj.activity_id = activity_id
    if grace_period_hours is not None:
        session_obj.grace_period_hours = grace_period_hours
    if end_date is not None:
        session_obj.end_date = end_date

    db.commit()
    db.refresh(session_obj)
    return session_obj
```

- [ ] **Step 18: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_schedule_service.py::test_update_session tests/test_schedule_service.py::test_update_session_not_found -v"`
Expected: All PASS

- [ ] **Step 19: Run all schedule service tests for regressions**

Run: `nix-shell --run "pytest tests/test_schedule_service.py -v"`
Expected: All PASS

- [ ] **Step 20: Commit**

```bash
git add backend/modules/schedule/service.py tests/test_schedule_service.py
git commit -m "feat(schedule): add create_session, get_session, list_sessions, update_session"
```

---

### Task 3: Routes — Ad-hoc Session Endpoints and Validation

**Files:**
- Modify: `backend/modules/schedule/routes.py`
- Modify: `tests/test_schedule_api.py`

- [ ] **Step 1: Write failing test for POST /sessions (ad-hoc real event)**

Add to `tests/test_schedule_api.py`:

```python
@pytest.mark.asyncio
async def test_create_adhoc_real_event(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/schedule/sessions",
        json={
            "start_date": "2026-04-15",
            "session_type": "real_event",
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["session_type"] == "real_event"
    assert data["season_id"] is None
    assert data["end_date"] is None
    assert data["status"] == "scheduled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "pytest tests/test_schedule_api.py::test_create_adhoc_real_event -v"`
Expected: FAIL — 404 (route doesn't exist)

- [ ] **Step 3: Write failing test for real event with season_id rejected**

Add to `tests/test_schedule_api.py`:

```python
@pytest.mark.asyncio
async def test_create_real_event_with_season_rejected(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    # First create a season to get a valid season_id
    season_resp = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": token},
    )
    season_id = season_resp.json()["id"]

    response = await test_client.post(
        "/api/schedule/sessions",
        json={
            "start_date": "2026-04-15",
            "session_type": "real_event",
            "season_id": season_id,
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 400
    assert "season" in response.json()["detail"].lower()
```

- [ ] **Step 4: Write failing test for GET /sessions with filters**

Add to `tests/test_schedule_api.py`:

```python
@pytest.mark.asyncio
async def test_list_sessions_with_filters(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)

    # Create a season (generates sessions)
    season_resp = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-09-10",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": token},
    )
    season_id = season_resp.json()["id"]

    # Create an ad-hoc real event
    await test_client.post(
        "/api/schedule/sessions",
        json={
            "start_date": "2026-04-15",
            "session_type": "real_event",
        },
        cookies={"access_token": token},
    )

    # List all sessions
    resp_all = await test_client.get(
        "/api/schedule/sessions",
        cookies={"access_token": token},
    )
    assert resp_all.status_code == 200
    all_sessions = resp_all.json()
    assert len(all_sessions) == 3  # 2 from season + 1 ad-hoc

    # Filter by season
    resp_season = await test_client.get(
        f"/api/schedule/sessions?season_id={season_id}",
        cookies={"access_token": token},
    )
    assert resp_season.status_code == 200
    assert len(resp_season.json()) == 2

    # Filter by status
    resp_status = await test_client.get(
        "/api/schedule/sessions?status=scheduled",
        cookies={"access_token": token},
    )
    assert resp_status.status_code == 200
    assert len(resp_status.json()) == 3
```

- [ ] **Step 5: Write failing test for GET /sessions/{id}**

Add to `tests/test_schedule_api.py`:

```python
@pytest.mark.asyncio
async def test_get_single_session(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    create_resp = await test_client.post(
        "/api/schedule/sessions",
        json={
            "start_date": "2026-04-15",
            "session_type": "real_event",
            "net_control_callsign": "W0NE",
        },
        cookies={"access_token": token},
    )
    session_id = create_resp.json()["id"]

    response = await test_client.get(
        f"/api/schedule/sessions/{session_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == session_id
    assert data["session_type"] == "real_event"


@pytest.mark.asyncio
async def test_get_session_not_found(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/schedule/sessions/9999",
        cookies={"access_token": token},
    )
    assert response.status_code == 404
```

- [ ] **Step 6: Write failing test for season validation**

Add to `tests/test_schedule_api.py`:

```python
@pytest.mark.asyncio
async def test_create_season_end_before_start_rejected(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Bad Season",
            "start_date": "2026-10-01",
            "end_date": "2026-09-01",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_season_no_day_of_week_rejected(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Bad Season",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "is_week_long": False,
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 400
```

- [ ] **Step 7: Run all new tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_schedule_api.py::test_create_adhoc_real_event tests/test_schedule_api.py::test_create_real_event_with_season_rejected tests/test_schedule_api.py::test_list_sessions_with_filters tests/test_schedule_api.py::test_get_single_session tests/test_schedule_api.py::test_get_session_not_found tests/test_schedule_api.py::test_create_season_end_before_start_rejected tests/test_schedule_api.py::test_create_season_no_day_of_week_rejected -v"`
Expected: FAIL

- [ ] **Step 8: Implement the new routes and validation**

Update `backend/modules/schedule/routes.py`:

1. Add new imports at the top:

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from backend.modules.schedule.service import (
    generate_sessions,
    create_session as create_session_service,
    get_session as get_session_service,
    list_sessions as list_sessions_service,
    update_session as update_session_service,
)
```

2. Add `SessionCreate` Pydantic schema after `SessionUpdate`:

```python
class SessionCreate(BaseModel):
    start_date: date
    end_date: date | None = None
    session_type: SessionType
    season_id: int | None = None
    grace_period_hours: float = 24.0
    net_control_callsign: str | None = None
    activity_id: int | None = None
```

3. Add the `_session_to_response` helper (extract from existing inline dicts):

```python
def _session_to_response(s: NetSession) -> dict:
    return {
        "id": s.id,
        "season_id": s.season_id,
        "start_date": s.start_date.isoformat(),
        "end_date": s.end_date.isoformat() if s.end_date else None,
        "grace_period_hours": s.grace_period_hours,
        "session_type": s.session_type.value,
        "status": s.status.value,
        "activity_id": s.activity_id,
        "net_control_callsign": s.net_control_callsign,
    }
```

4. Add validation to `create_season` route (at the start of the function body, before creating the season object):

```python
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="end_date must not be before start_date")
    if not body.is_week_long and body.day_of_week is None:
        raise HTTPException(status_code=400, detail="day_of_week is required for non-week-long seasons")
```

5. Add new session routes:

```python
@schedule_router.post("/sessions", status_code=201)
async def create_session_route(
    body: SessionCreate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    if body.session_type == SessionType.REAL_EVENT and body.season_id is not None:
        raise HTTPException(status_code=400, detail="Real event sessions cannot belong to a season")

    session_obj = create_session_service(
        db,
        start_date=body.start_date,
        session_type=body.session_type,
        end_date=body.end_date,
        season_id=body.season_id,
        grace_period_hours=body.grace_period_hours,
        net_control_callsign=body.net_control_callsign,
        activity_id=body.activity_id,
    )
    return _session_to_response(session_obj)


@schedule_router.get("/sessions")
async def list_sessions_route(
    season_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    status_enum = None
    if status is not None:
        try:
            status_enum = SessionStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    sessions = list_sessions_service(db, season_id=season_id, status=status_enum)
    return [_session_to_response(s) for s in sessions]


@schedule_router.get("/sessions/{session_id}")
async def get_session_route(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    session_obj = get_session_service(db, session_id)
    if session_obj is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(session_obj)
```

6. Update the existing `update_session` route to use the new service function and `_session_to_response` helper. Add `end_date` to `SessionUpdate`:

```python
class SessionUpdate(BaseModel):
    status: SessionStatus | None = None
    session_type: SessionType | None = None
    net_control_callsign: str | None = None
    activity_id: int | None = None
    grace_period_hours: float | None = None
    end_date: date | None = None
```

Update the route handler:

```python
@schedule_router.patch("/sessions/{session_id}")
async def update_session(
    session_id: int,
    body: SessionUpdate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    session_obj = update_session_service(
        db,
        session_id,
        status=body.status,
        session_type=body.session_type,
        net_control_callsign=body.net_control_callsign,
        activity_id=body.activity_id,
        grace_period_hours=body.grace_period_hours,
        end_date=body.end_date,
    )
    if session_obj is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(session_obj)
```

7. Update `_season_to_response` and `list_sessions` (the season's sessions list) to use `_session_to_response`:

```python
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
        "sessions": [_session_to_response(s) for s in season.sessions],
    }
```

And update `list_sessions` route for season sessions:

```python
@schedule_router.get("/seasons/{season_id}/sessions")
async def list_season_sessions(
    season_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    season = db.get(NetSeason, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return [_session_to_response(s) for s in season.sessions]
```

- [ ] **Step 9: Run all new tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_schedule_api.py::test_create_adhoc_real_event tests/test_schedule_api.py::test_create_real_event_with_season_rejected tests/test_schedule_api.py::test_list_sessions_with_filters tests/test_schedule_api.py::test_get_single_session tests/test_schedule_api.py::test_get_session_not_found tests/test_schedule_api.py::test_create_season_end_before_start_rejected tests/test_schedule_api.py::test_create_season_no_day_of_week_rejected -v"`
Expected: All PASS

- [ ] **Step 10: Run all schedule tests for regressions**

Run: `nix-shell --run "pytest tests/test_schedule_models.py tests/test_schedule_service.py tests/test_schedule_api.py -v"`
Expected: All PASS

- [ ] **Step 11: Commit**

```bash
git add backend/modules/schedule/routes.py tests/test_schedule_api.py
git commit -m "feat(schedule): add ad-hoc session routes, GET /sessions, season validation"
```

---

### Task 4: Alembic Migration

**Files:**
- Create: `alembic/versions/*_update_schedule_for_real_events.py`

- [ ] **Step 1: Generate the migration**

Run: `nix-shell --run "alembic revision --autogenerate -m 'update schedule for real events'"`
Expected: New migration file created in `alembic/versions/`

- [ ] **Step 2: Review the generated migration**

Read the generated file. It should contain:
- ALTER `net_sessions.season_id` to nullable
- ALTER `net_sessions.end_date` to nullable
- Modify the `sessiontype` enum to add `REAL_EVENT`

Note: SQLite doesn't support ALTER COLUMN or enum modification. If using SQLite for dev, the migration may need batch mode. Alembic's `render_as_batch=True` in `env.py` handles this. Verify `alembic/env.py` has this setting.

- [ ] **Step 3: Edit migration if needed for SQLite compatibility**

If the autogenerated migration doesn't use batch mode for SQLite, wrap the column alterations in `with op.batch_alter_table(...)` blocks. For the enum change, since SQLite stores enums as strings, no enum modification is needed — only PostgreSQL would need it.

- [ ] **Step 4: Test migration runs cleanly**

Run: `nix-shell --run "alembic upgrade head"`
Expected: Migration applies without errors.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/*_update_schedule_for_real_events.py
git commit -m "feat(schedule): add migration for real event support"
```

---

### Task 5: Full Test Suite Verification

- [ ] **Step 1: Run the complete test suite**

Run: `nix-shell --run "pytest -v"`
Expected: All tests pass, including schedule, reminders, checkins, and any other modules that reference NetSession.

- [ ] **Step 2: If any failures, fix and re-run**

Likely failure points:
- Other modules that assume `session.end_date` is never None (e.g., reminders `generate_due_drafts` compares `session.start_date`). These should still work since real events skip the reminder flow, but verify.
- Modules that assume `session.season_id` is always set. Check that `session.season` being None doesn't cause attribute errors in downstream code.

- [ ] **Step 3: Commit any fixes**

```bash
git add -u
git commit -m "fix(schedule): resolve downstream compatibility with nullable fields"
```
