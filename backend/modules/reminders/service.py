from __future__ import annotations

import calendar

import jinja2
from sqlalchemy.orm import Session

from backend.modules.activities.models import Activity
from backend.modules.reminders.models import ReminderTemplate, TemplateType
from backend.modules.schedule.models import NetSession, SessionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_default(db: Session, template_type: TemplateType) -> None:
    """Clear is_default on all existing templates of the given type."""
    db.query(ReminderTemplate).filter(
        ReminderTemplate.template_type == template_type,
        ReminderTemplate.is_default.is_(True),
    ).update({"is_default": False})


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------

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

    tmpl = ReminderTemplate(
        name=name,
        template_type=template_type,
        subject_template=subject_template,
        body_template=body_template,
        lead_time_days=lead_time_days,
        is_default=is_default,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return tmpl


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
    tmpl = db.get(ReminderTemplate, template_id)
    if tmpl is None:
        return None

    if is_default is True:
        effective_type = template_type if template_type is not None else tmpl.template_type
        _clear_default(db, effective_type)

    if name is not None:
        tmpl.name = name
    if template_type is not None:
        tmpl.template_type = template_type
    if subject_template is not None:
        tmpl.subject_template = subject_template
    if body_template is not None:
        tmpl.body_template = body_template
    if lead_time_days is not None:
        tmpl.lead_time_days = lead_time_days
    if is_default is not None:
        tmpl.is_default = is_default

    db.commit()
    db.refresh(tmpl)
    return tmpl


def delete_template(db: Session, template_id: int) -> bool:
    tmpl = db.get(ReminderTemplate, template_id)
    if tmpl is None:
        return False
    if tmpl.is_default:
        return False
    db.delete(tmpl)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

_DAY_NAMES = list(calendar.day_name)  # Monday=0 … Sunday=6


def _build_next_week_preview(db: Session, current_session: NetSession) -> str:
    """Return a preview string for the next session in the same season."""
    next_session = (
        db.query(NetSession)
        .filter(
            NetSession.season_id == current_session.season_id,
            NetSession.start_date > current_session.start_date,
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


def build_template_context(db: Session, net_session: NetSession) -> dict:
    """Build the Jinja2 context dict for a given NetSession."""
    season = net_session.season

    # Date formatting
    date_str = net_session.start_date.strftime("%-B %-d, %Y")
    # Use platform-portable strftime for month/day without leading zeros
    # strftime("%-B") is Linux-specific; construct manually for portability
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

    # Day of week from season integer (0=Monday, 6=Sunday)
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

    return {
        "date": date_str,
        "time": time_str,
        "day_of_week": day_of_week,
        "activity_title": activity_title,
        "activity_instructions": activity_instructions,
        "net_control": net_control,
        "next_week_preview": next_week_preview,
    }


def render_reminder(
    template: ReminderTemplate,
    context: dict,
) -> tuple[str, str]:
    """Render subject and body from a ReminderTemplate and context dict."""
    env = jinja2.Environment(undefined=jinja2.Undefined)

    subject = env.from_string(template.subject_template).render(context)

    try:
        body = env.from_string(template.body_template).render(context)
    except jinja2.TemplateError as exc:
        body = f"Template rendering error: {exc}"

    return subject, body
