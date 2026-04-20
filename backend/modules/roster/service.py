from __future__ import annotations

import calendar
from datetime import date as date_type, datetime, timezone

import jinja2
from sqlalchemy.orm import Session

from backend.modules.activities.models import Activity
from backend.modules.checkins.models import CheckIn
from backend.modules.roster.models import RosterTemplate, RosterLog, RosterStatus
from backend.modules.schedule.models import NetSession, SessionStatus, SessionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> date_type:
    """Return today's date. Extracted for test mocking."""
    return date_type.today()


def _clear_default(db: Session) -> None:
    """Clear is_default on all roster templates."""
    db.query(RosterTemplate).filter(
        RosterTemplate.is_default.is_(True),
    ).update({"is_default": False})


_DAY_NAMES = list(calendar.day_name)  # Monday=0 … Sunday=6


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------

def create_template(
    db: Session,
    name: str,
    subject_template: str,
    header_template: str,
    welcome_template: str,
    comments_template: str,
    footer_template: str,
    lead_time_days: int = 1,
    is_default: bool = False,
) -> RosterTemplate:
    if is_default:
        _clear_default(db)

    tmpl = RosterTemplate(
        name=name,
        subject_template=subject_template,
        header_template=header_template,
        welcome_template=welcome_template,
        comments_template=comments_template,
        footer_template=footer_template,
        lead_time_days=lead_time_days,
        is_default=is_default,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return tmpl


def get_template(db: Session, template_id: int) -> RosterTemplate | None:
    return db.get(RosterTemplate, template_id)


def list_templates(db: Session) -> list[RosterTemplate]:
    return db.query(RosterTemplate).order_by(RosterTemplate.name).all()


def update_template(
    db: Session,
    template_id: int,
    name: str | None = None,
    subject_template: str | None = None,
    header_template: str | None = None,
    welcome_template: str | None = None,
    comments_template: str | None = None,
    footer_template: str | None = None,
    lead_time_days: int | None = None,
    is_default: bool | None = None,
) -> RosterTemplate | None:
    tmpl = db.get(RosterTemplate, template_id)
    if tmpl is None:
        return None

    if is_default is True:
        _clear_default(db)

    if name is not None:
        tmpl.name = name
    if subject_template is not None:
        tmpl.subject_template = subject_template
    if header_template is not None:
        tmpl.header_template = header_template
    if welcome_template is not None:
        tmpl.welcome_template = welcome_template
    if comments_template is not None:
        tmpl.comments_template = comments_template
    if footer_template is not None:
        tmpl.footer_template = footer_template
    if lead_time_days is not None:
        tmpl.lead_time_days = lead_time_days
    if is_default is not None:
        tmpl.is_default = is_default

    db.commit()
    db.refresh(tmpl)
    return tmpl


def delete_template(db: Session, template_id: int) -> bool:
    tmpl = db.get(RosterTemplate, template_id)
    if tmpl is None:
        return False
    if tmpl.is_default:
        return False
    db.delete(tmpl)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Context Building
# ---------------------------------------------------------------------------

def _build_next_week_preview(db: Session, current_session: NetSession) -> str:
    """Return a preview string for the next session in the same season."""
    if current_session.season_id is None:
        return ""

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
        return "Activity"

    return "Standard Winlink Check-in"


def build_roster_context(db: Session, net_session: NetSession) -> dict:
    """Build the Jinja2 context dict for roster templates."""
    season = net_session.season

    # Date formatting
    month_name = net_session.start_date.strftime("%B")
    day = net_session.start_date.day
    year = net_session.start_date.year
    date_str = f"{month_name} {day}, {year}"

    # Time formatting (from season)
    time_str = ""
    if season and season.time is not None:
        hour = season.time.hour
        minute = season.time.minute
        period = "AM" if hour < 12 else "PM"
        display_hour = hour % 12 or 12
        if minute:
            time_str = f"{display_hour}:{minute:02d} {period}"
        else:
            time_str = f"{display_hour}:00 {period}"

    # Day of week
    day_of_week = ""
    if season and season.day_of_week is not None:
        day_of_week = _DAY_NAMES[season.day_of_week]

    # Activity fields
    activity_title = ""
    activity_instructions = ""
    if net_session.session_type == SessionType.ACTIVITY and net_session.activity_id:
        activity = db.get(Activity, net_session.activity_id)
        if activity:
            activity_title = activity.title
            activity_instructions = activity.instructions

    net_control = net_session.net_control_callsign or ""
    next_week_preview = _build_next_week_preview(db, net_session)

    # Query check-ins ordered by name
    checkins_query = (
        db.query(CheckIn)
        .filter(CheckIn.session_id == net_session.id)
        .order_by(CheckIn.name)
        .all()
    )

    checkins = []
    new_members = []
    has_gps = False
    for ci in checkins_query:
        ci_dict = {
            "name": ci.name,
            "callsign": ci.callsign,
            "city": ci.city or "",
            "county": ci.county or "",
            "state": ci.state or "",
            "mode": ci.mode,
            "comments": ci.comments or "",
            "is_new_member": ci.is_new_member,
        }
        checkins.append(ci_dict)
        if ci.is_new_member:
            new_members.append(ci_dict)
        if ci.latitude is not None and ci.longitude is not None:
            has_gps = True

    map_url = ""
    if has_gps:
        map_url = f"/api/roster/session/{net_session.id}/geojson"

    return {
        "date": date_str,
        "time": time_str,
        "day_of_week": day_of_week,
        "activity_title": activity_title,
        "activity_instructions": activity_instructions,
        "net_control": net_control,
        "next_week_preview": next_week_preview,
        "checkins": checkins,
        "new_members": new_members,
        "total_count": len(checkins),
        "map_url": map_url,
    }
