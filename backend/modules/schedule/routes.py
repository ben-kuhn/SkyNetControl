from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, require_role, require_scope
from backend.auth.models import User, UserRole
from backend.config_mgmt.service import get_config_value
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionStatus,
    SessionType,
)
from backend.modules.schedule.service import (
    generate_sessions,
    create_session as create_session_service,
    get_session as get_session_service,
    list_sessions as list_sessions_service,
    update_session as update_session_service,
)

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
    season_id: int | None
    start_date: date
    end_date: date | None
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
    status: SessionStatus | None = None
    session_type: SessionType | None = None
    net_control_callsign: str | None = None
    activity_id: int | None = None
    grace_period_hours: float | None = None
    end_date: date | None = None


class SessionCreate(BaseModel):
    start_date: date
    end_date: date | None = None
    session_type: SessionType
    season_id: int | None = None
    grace_period_hours: float = 24.0
    net_control_callsign: str | None = None
    activity_id: int | None = None


# --- Helpers ---


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


# --- Routes ---


@schedule_router.post("/seasons", status_code=201)
async def create_season(
    body: SeasonCreate,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="end_date must not be before start_date")
    if not body.is_week_long and body.day_of_week is None:
        raise HTTPException(status_code=400, detail="day_of_week is required for non-week-long seasons")

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
async def list_season_sessions(
    season_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    season = db.get(NetSeason, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return [_session_to_response(s) for s in season.sessions]


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
    user: User = Depends(require_scope("schedule:read")),
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
