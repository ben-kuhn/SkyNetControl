from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.modules.nets.models import NetRole
from backend.modules.reminders.models import ReminderLog, ReminderStatus, ReminderTemplate, TemplateType
from backend.modules.reminders.service import (
    approve_reminder,
    create_template,
    delete_template,
    generate_draft,
    generate_due_drafts,
    get_template,
    list_templates,
    mark_sent,
    regenerate_draft,
    skip_reminder,
    update_draft,
    update_template,
    _get_net_id_for_session,
)
from backend.modules.schedule.models import NetSession, NetSeason

reminders_router = APIRouter(prefix="/api/nets/{net_slug}/reminders", tags=["reminders"])


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
        "net_id": template.net_id,
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


def _verify_log_net(db: Session, log: ReminderLog, net_id: int) -> bool:
    """Return True iff log's session belongs to *net_id*."""
    net_session = db.get(NetSession, log.session_id)
    if net_session is None:
        return False
    return _get_net_id_for_session(db, net_session) == net_id


# --- Template routes ---


@reminders_router.post("/templates", status_code=201)
async def create_template_route(
    body: TemplateCreate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    template = create_template(
        db,
        net_id=ctx.net.id,
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
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    templates = list_templates(db, net_id=ctx.net.id)
    return [_template_to_response(t) for t in templates]


@reminders_router.get("/template-defaults")
async def template_defaults_route(
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
):
    """Return the shipped seed templates so the "+ New template" UI can
    pre-fill from pristine originals, even after operators have edited
    their installed defaults."""
    from backend.modules.reminders.seeds import SEED_REMINDER_TEMPLATES

    return SEED_REMINDER_TEMPLATES


@reminders_router.patch("/templates/{template_id}")
async def update_template_route(
    template_id: int,
    body: TemplateUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    template = update_template(
        db,
        template_id,
        net_id=ctx.net.id,
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
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    template = get_template(db, template_id, net_id=ctx.net.id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default template")
    delete_template(db, template_id, net_id=ctx.net.id)


# --- Generation routes ---


@reminders_router.post("/generate")
async def generate_due_drafts_route(
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    reminders = generate_due_drafts(db, net_id=ctx.net.id)
    return {
        "generated": len(reminders),
        "reminders": [_reminder_to_response(r) for r in reminders],
    }


@reminders_router.post("/generate/{session_id}")
async def generate_draft_route(
    session_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    # Cross-net: verify session belongs to this net
    net_session = db.get(NetSession, session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if _get_net_id_for_session(db, net_session) != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")

    log = generate_draft(db, session_id, net_id=ctx.net.id)
    if log is None:
        raise HTTPException(status_code=404, detail="Session not found or no default template")

    from backend.modules.notifications.models import NotificationKind
    from backend.modules.notifications.service import (
        _format_session_date,
        create_notification,
        resolve_session_recipient,
    )

    recipient = resolve_session_recipient(db, net_session)
    if recipient is not None:
        create_notification(
            db,
            recipient_callsign=recipient,
            kind=NotificationKind.REMINDER_DRAFT,
            message=f"Reminder draft ready for {_format_session_date(net_session)}",
            link_url="/reminders",
            session_id=net_session.id,
        )

    return _reminder_to_response(log)


# --- Reminder list and detail routes ---


@reminders_router.get("/")
async def list_reminders_route(
    status: str | None = Query(default=None),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    query = (
        db.query(ReminderLog)
        .join(NetSession, ReminderLog.session_id == NetSession.id)
        .join(NetSeason, NetSession.season_id == NetSeason.id)
        .filter(NetSeason.net_id == ctx.net.id)
    )
    if status is not None:
        try:
            status_enum = ReminderStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        query = query.filter(ReminderLog.status == status_enum)
    logs = query.order_by(ReminderLog.drafted_at.desc()).all()
    return [_reminder_to_response(log) for log in logs]


@reminders_router.get("/session/{session_id}")
async def get_reminder_for_session_route(
    session_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    # Cross-net: verify session belongs to this net
    net_session = db.get(NetSession, session_id)
    if net_session is None or _get_net_id_for_session(db, net_session) != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")

    log = db.query(ReminderLog).filter(ReminderLog.session_id == session_id).first()
    if log is None:
        raise HTTPException(status_code=404, detail="Reminder not found for session")
    return _reminder_to_response(log)


# --- Reminder action routes ---


@reminders_router.patch("/{reminder_id}")
async def update_draft_route(
    reminder_id: int,
    body: DraftUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = db.get(ReminderLog, reminder_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Reminder not found")
    result = update_draft(
        db,
        reminder_id,
        content_subject=body.content_subject,
        content_body=body.content_body,
    )
    if result is None:
        raise HTTPException(status_code=409, detail="Reminder not in draft status")
    return _reminder_to_response(result)


@reminders_router.post("/{reminder_id}/approve")
async def approve_reminder_route(
    reminder_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = db.get(ReminderLog, reminder_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Reminder not found")
    result = approve_reminder(db, reminder_id, approver_callsign=ctx.user.callsign)
    if result is None:
        raise HTTPException(status_code=409, detail="Reminder not in draft status")
    return _reminder_to_response(result)


@reminders_router.post("/{reminder_id}/send")
async def mark_sent_route(
    reminder_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = db.get(ReminderLog, reminder_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Reminder not found")
    result = mark_sent(db, reminder_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Reminder not in approved status")
    return _reminder_to_response(result)


@reminders_router.post("/{reminder_id}/skip")
async def skip_reminder_route(
    reminder_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = db.get(ReminderLog, reminder_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Reminder not found")
    result = skip_reminder(db, reminder_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Reminder not in skippable status")
    return _reminder_to_response(result)


@reminders_router.post("/{reminder_id}/regenerate")
async def regenerate_reminder_route(
    reminder_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = db.get(ReminderLog, reminder_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Reminder not found")
    result = regenerate_draft(db, reminder_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Reminder not in draft status")
    return _reminder_to_response(result)
