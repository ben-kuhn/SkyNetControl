from __future__ import annotations

import calendar
from datetime import date as date_type, datetime, timezone

import jinja2
from sqlalchemy.orm import Session

from backend.config import settings
from backend.config_mgmt.service import get_config_value
from backend.modules.activities.models import Activity
from backend.modules.checkins.models import CheckIn
from backend.modules.checkins.service import purge_session_source_files
from backend.modules.notifications.models import NotificationKind
from backend.modules.notifications.service import (
    _format_session_date,
    create_notification,
    resolve_session_recipient,
)
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
    def _fmt(d):
        return f"{d.strftime('%B')} {d.day}, {d.year}"

    start_date_str = _fmt(net_session.start_date)
    end_date_str = _fmt(net_session.end_date) if net_session.end_date else start_date_str
    date_str = start_date_str

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
    checkins_query = db.query(CheckIn).filter(CheckIn.session_id == net_session.id).order_by(CheckIn.name).all()

    checkins = []
    new_members = []
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

    session_url = f"{settings.app_base_url}/checkins?session={net_session.id}"

    net_callsign = get_config_value(db, "default_net_control", default="") or ""
    net_address = get_config_value(db, "net_address", default="") or ""

    return {
        "date": date_str,
        "start_date": start_date_str,
        "end_date": end_date_str,
        "time": time_str,
        "day_of_week": day_of_week,
        "activity_title": activity_title,
        "activity_instructions": activity_instructions,
        "net_control": net_control,
        "net_callsign": net_callsign,
        "net_address": net_address,
        "next_week_preview": next_week_preview,
        "checkins": checkins,
        "new_members": new_members,
        "total_count": len(checkins),
        "session_url": session_url,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_roster(
    template: RosterTemplate,
    context: dict,
) -> dict[str, str]:
    """Render all five template sections. Returns dict with keys: subject, header, welcome, comments, footer."""
    env = jinja2.Environment(undefined=jinja2.Undefined)
    sections = {}

    for key, attr in [
        ("subject", "subject_template"),
        ("header", "header_template"),
        ("welcome", "welcome_template"),
        ("comments", "comments_template"),
        ("footer", "footer_template"),
    ]:
        try:
            sections[key] = env.from_string(getattr(template, attr)).render(context)
        except jinja2.TemplateError as exc:
            sections[key] = f"Template rendering error: {exc}"

    return sections


# ---------------------------------------------------------------------------
# Draft Generation
# ---------------------------------------------------------------------------


def generate_draft(
    db: Session,
    session_id: int,
    template_id: int | None = None,
) -> RosterLog | None:
    """Create a DRAFT RosterLog for the given session. Idempotent."""
    existing = db.query(RosterLog).filter(RosterLog.session_id == session_id).first()
    if existing is not None:
        return existing

    net_session = db.get(NetSession, session_id)
    if net_session is None:
        return None

    if template_id is not None:
        template = db.get(RosterTemplate, template_id)
    else:
        template = db.query(RosterTemplate).filter(RosterTemplate.is_default.is_(True)).first()

    if template is None:
        return None

    context = build_roster_context(db, net_session)
    sections = render_roster(template, context)

    log = RosterLog(
        session_id=session_id,
        template_id=template.id,
        status=RosterStatus.DRAFT,
        content_subject=sections["subject"],
        content_header=sections["header"],
        content_welcome=sections["welcome"],
        content_comments=sections["comments"],
        content_footer=sections["footer"],
        session_url=context["session_url"] or None,
        drafted_at=datetime.now(tz=timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def generate_due_drafts(db: Session) -> list[RosterLog]:
    """Generate drafts for completed sessions past their lead time without a roster."""
    today = _today()

    default_template = db.query(RosterTemplate).filter(RosterTemplate.is_default.is_(True)).first()
    if default_template is None:
        return []

    existing_session_ids = {row[0] for row in db.query(RosterLog.session_id).all()}

    completed_sessions = db.query(NetSession).filter(NetSession.status == SessionStatus.COMPLETED).all()

    drafts: list[RosterLog] = []
    for session in completed_sessions:
        if session.id in existing_session_ids:
            continue
        # Skip sessions without end_date (real events need manual generation)
        if session.end_date is None:
            continue

        days_since = (today - session.end_date).days
        if days_since >= default_template.lead_time_days:
            log = generate_draft(db, session.id, template_id=default_template.id)
            if log is not None:
                recipient = resolve_session_recipient(db, session)
                if recipient is not None:
                    create_notification(
                        db,
                        recipient_callsign=recipient,
                        kind=NotificationKind.ROSTER_DRAFT,
                        message=f"Roster draft ready for {_format_session_date(session)}",
                        link_url="/roster",
                        session_id=session.id,
                    )
                drafts.append(log)

    return drafts


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def assemble_roster(db: Session, roster_id: int) -> str | None:
    """Assemble the full plain-text roster from prose sections and current check-in data."""
    log = db.get(RosterLog, roster_id)
    if log is None:
        return None

    checkins = db.query(CheckIn).filter(CheckIn.session_id == log.session_id).order_by(CheckIn.name).all()

    table_lines = []
    for ci in checkins:
        marker = " *" if ci.is_new_member else ""
        parts = [ci.name, ci.callsign]
        if ci.city:
            parts.append(ci.city)
        if ci.county:
            parts.append(ci.county)
        if ci.state:
            parts.append(ci.state)
        parts.append(ci.mode)
        if ci.comments:
            parts.append(ci.comments)
        table_lines.append(" | ".join(parts) + marker)

    table = "\n".join(table_lines)

    parts = [log.content_subject, "", log.content_header]
    if table:
        parts.extend(["", table])
    if log.content_welcome.strip():
        parts.extend(["", log.content_welcome])
    if log.content_comments.strip():
        parts.extend(["", log.content_comments])
    parts.extend(["", log.content_footer])

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Status Transitions
# ---------------------------------------------------------------------------


def approve_roster(
    db: Session,
    roster_id: int,
    approver_callsign: str,
) -> RosterLog | None:
    """Transition DRAFT → APPROVED. Finalizes member records via approve_session_checkins."""
    from backend.modules.checkins.service import approve_session_checkins

    log = db.get(RosterLog, roster_id)
    if log is None or log.status != RosterStatus.DRAFT:
        return None

    approve_session_checkins(db, log.session_id)

    log.status = RosterStatus.APPROVED
    log.approved_by = approver_callsign
    log.approved_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(log)
    return log


def mark_sent(db: Session, roster_id: int) -> RosterLog | None:
    """Transition APPROVED → SENT via delivery backends."""
    log = db.get(RosterLog, roster_id)
    if log is None or log.status != RosterStatus.APPROVED:
        return None

    from backend.integrations.delivery.service import dispatch_delivery

    assembled = assemble_roster(db, roster_id)
    body = assembled if assembled else ""

    delivered = dispatch_delivery(db, "roster", log.id, log.content_subject, body)
    if not delivered:
        net_session = db.get(NetSession, log.session_id)
        if net_session is not None:
            recipient = resolve_session_recipient(db, net_session)
            if recipient is not None:
                create_notification(
                    db,
                    recipient_callsign=recipient,
                    kind=NotificationKind.DELIVERY_FAILURE,
                    message=f"Send failed for roster on {_format_session_date(net_session)} — verify delivery backends",
                    link_url="/config",
                    session_id=net_session.id,
                    dedupe=False,
                )
        return None  # stay APPROVED so user can retry

    log.status = RosterStatus.SENT
    log.sent_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(log)
    purge_session_source_files(db, log.session_id)
    return log


def skip_roster(db: Session, roster_id: int) -> RosterLog | None:
    """Transition DRAFT or APPROVED → SKIPPED."""
    log = db.get(RosterLog, roster_id)
    if log is None or log.status not in (RosterStatus.DRAFT, RosterStatus.APPROVED):
        return None

    log.status = RosterStatus.SKIPPED
    db.commit()
    db.refresh(log)
    purge_session_source_files(db, log.session_id)
    return log


def update_draft(
    db: Session,
    roster_id: int,
    content_subject: str | None = None,
    content_header: str | None = None,
    content_welcome: str | None = None,
    content_comments: str | None = None,
    content_footer: str | None = None,
) -> RosterLog | None:
    """Edit prose sections while status is DRAFT."""
    log = db.get(RosterLog, roster_id)
    if log is None or log.status != RosterStatus.DRAFT:
        return None

    if content_subject is not None:
        log.content_subject = content_subject
    if content_header is not None:
        log.content_header = content_header
    if content_welcome is not None:
        log.content_welcome = content_welcome
    if content_comments is not None:
        log.content_comments = content_comments
    if content_footer is not None:
        log.content_footer = content_footer

    db.commit()
    db.refresh(log)
    return log


def regenerate_draft(db: Session, roster_id: int) -> RosterLog | None:
    """Re-render a DRAFT roster against the current session, check-ins, and template.

    Returns the updated log, or None if the roster is missing or not in DRAFT status.
    """
    log = db.get(RosterLog, roster_id)
    if log is None or log.status != RosterStatus.DRAFT:
        return None

    net_session = db.get(NetSession, log.session_id)
    if net_session is None:
        return None

    template = None
    if log.template_id is not None:
        template = db.get(RosterTemplate, log.template_id)
    if template is None:
        template = db.query(RosterTemplate).filter(RosterTemplate.is_default.is_(True)).first()
    if template is None:
        return None

    context = build_roster_context(db, net_session)
    sections = render_roster(template, context)
    log.content_subject = sections["subject"]
    log.content_header = sections["header"]
    log.content_welcome = sections["welcome"]
    log.content_comments = sections["comments"]
    log.content_footer = sections["footer"]
    log.session_url = context["session_url"] or None
    db.commit()
    db.refresh(log)
    return log
