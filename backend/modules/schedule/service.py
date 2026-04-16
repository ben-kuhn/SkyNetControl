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
