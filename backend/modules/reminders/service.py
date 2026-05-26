from __future__ import annotations

import calendar
from datetime import date as date_type, datetime, timezone

import jinja2
from sqlalchemy.orm import Session

from backend.modules.activities.models import Activity
from backend.modules.reminders.models import ReminderTemplate, ReminderLog, ReminderStatus, TemplateType
from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
from backend.modules.notifications.models import NotificationKind
from backend.modules.notifications.service import (
    _format_session_date,
    create_notification,
    resolve_session_recipient,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today() -> date_type:
    """Return today's date. Extracted for test mocking."""
    return date_type.today()


def _session_type_to_template_type(session_type: SessionType) -> TemplateType:
    if session_type == SessionType.ACTIVITY:
        return TemplateType.ACTIVITY
    return TemplateType.REGULAR_CHECKIN


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


def build_template_context(db: Session, net_session: NetSession) -> dict:
    """Build the Jinja2 context dict for a given NetSession."""
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

    try:
        subject = env.from_string(template.subject_template).render(context)
    except jinja2.TemplateError as exc:
        subject = f"Template rendering error: {exc}"

    try:
        body = env.from_string(template.body_template).render(context)
    except jinja2.TemplateError as exc:
        body = f"Template rendering error: {exc}"

    return subject, body


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------


def generate_draft(
    db: Session,
    session_id: int,
    template_id: int | None = None,
) -> ReminderLog | None:
    """Create a DRAFT ReminderLog for the given session. Idempotent."""
    # Idempotency: return existing log if one already exists for this session
    existing = db.query(ReminderLog).filter(ReminderLog.session_id == session_id).first()
    if existing is not None:
        return existing

    net_session = db.get(NetSession, session_id)
    if net_session is None:
        return None

    if template_id is not None:
        template = db.get(ReminderTemplate, template_id)
    else:
        tmpl_type = _session_type_to_template_type(net_session.session_type)
        template = (
            db.query(ReminderTemplate)
            .filter(
                ReminderTemplate.template_type == tmpl_type,
                ReminderTemplate.is_default.is_(True),
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
        drafted_at=datetime.now(tz=timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def generate_due_drafts(db: Session) -> list[ReminderLog]:
    """Generate drafts for all SCHEDULED sessions that are within their lead time."""
    today = _today()

    # Get all default templates to determine lead times per type
    default_templates: dict[TemplateType, ReminderTemplate] = {}
    for tmpl in db.query(ReminderTemplate).filter(ReminderTemplate.is_default.is_(True)).all():
        default_templates[tmpl.template_type] = tmpl

    # Find all SCHEDULED sessions that don't yet have a ReminderLog
    existing_session_ids = {row[0] for row in db.query(ReminderLog.session_id).all()}

    scheduled_sessions = db.query(NetSession).filter(NetSession.status == SessionStatus.SCHEDULED).all()

    drafts: list[ReminderLog] = []
    for session in scheduled_sessions:
        if session.id in existing_session_ids:
            continue

        tmpl_type = _session_type_to_template_type(session.session_type)
        template = default_templates.get(tmpl_type)
        if template is None:
            continue

        days_until = (session.start_date - today).days
        if days_until <= template.lead_time_days:
            log = generate_draft(db, session.id, template_id=template.id)
            if log is not None:
                drafts.append(log)
                recipient = resolve_session_recipient(db, session)
                if recipient is not None:
                    create_notification(
                        db,
                        recipient_callsign=recipient,
                        kind=NotificationKind.REMINDER_DRAFT,
                        message=f"Reminder draft ready for {_format_session_date(session)}",
                        link_url="/reminders",
                        session_id=session.id,
                    )

    return drafts


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def approve_reminder(
    db: Session,
    reminder_id: int,
    approver_callsign: str,
) -> ReminderLog | None:
    """Transition a DRAFT reminder to APPROVED."""
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status != ReminderStatus.DRAFT:
        return None

    log.status = ReminderStatus.APPROVED
    log.approved_by = approver_callsign
    log.approved_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(log)
    return log


def mark_sent(db: Session, reminder_id: int) -> ReminderLog | None:
    """Transition an APPROVED reminder to SENT via delivery backends."""
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status != ReminderStatus.APPROVED:
        return None

    from backend.integrations.delivery.service import dispatch_delivery

    delivered = dispatch_delivery(
        db, "reminder", log.id, log.content_subject, log.content_body
    )
    if not delivered:
        net_session = db.get(NetSession, log.session_id)
        if net_session is not None:
            recipient = resolve_session_recipient(db, net_session)
            if recipient is not None:
                create_notification(
                    db,
                    recipient_callsign=recipient,
                    kind=NotificationKind.DELIVERY_FAILURE,
                    message=f"Send failed for reminder on {_format_session_date(net_session)} — verify delivery backends",
                    link_url="/config",
                    session_id=net_session.id,
                    dedupe=False,
                )
        return None  # stay APPROVED so user can retry

    log.status = ReminderStatus.SENT
    log.sent_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(log)
    return log


def skip_reminder(db: Session, reminder_id: int) -> ReminderLog | None:
    """Transition a DRAFT or APPROVED reminder to SKIPPED."""
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
    """Edit the subject/body of a DRAFT reminder."""
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


def regenerate_draft(db: Session, reminder_id: int) -> ReminderLog | None:
    """Re-render a DRAFT reminder against the current session + template.

    Returns the updated log, or None if the reminder is missing or not in DRAFT status.
    """
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status != ReminderStatus.DRAFT:
        return None

    net_session = db.get(NetSession, log.session_id)
    if net_session is None:
        return None

    template = None
    if log.template_id is not None:
        template = db.get(ReminderTemplate, log.template_id)
    if template is None:
        tmpl_type = _session_type_to_template_type(net_session.session_type)
        template = (
            db.query(ReminderTemplate)
            .filter(
                ReminderTemplate.template_type == tmpl_type,
                ReminderTemplate.is_default.is_(True),
            )
            .first()
        )
    if template is None:
        return None

    context = build_template_context(db, net_session)
    subject, body = render_reminder(template, context)
    log.content_subject = subject
    log.content_body = body
    db.commit()
    db.refresh(log)
    return log
