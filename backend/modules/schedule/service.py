from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)


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


def get_session(db: Session, session_id: int) -> NetSession | None:
    return db.get(NetSession, session_id)


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
