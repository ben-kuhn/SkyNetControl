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
