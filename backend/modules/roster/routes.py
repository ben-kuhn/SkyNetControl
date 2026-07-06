from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.modules.nets.models import NetRole
from backend.modules.roster.models import RosterLog, RosterStatus
from backend.modules.roster.service import (
    approve_roster as approve_roster_service,
    assemble_roster as assemble_roster_service,
    create_template as create_template_service,
    delete_template as delete_template_service,
    generate_draft as generate_draft_service,
    generate_due_drafts as generate_due_drafts_service,
    get_template as get_template_service,
    list_templates as list_templates_service,
    mark_sent as mark_sent_service,
    regenerate_draft as regenerate_draft_service,
    resend_roster as resend_roster_service,
    skip_roster as skip_roster_service,
    update_draft as update_draft_service,
    update_template as update_template_service,
    _get_net_id_for_session,
)
from backend.modules.schedule.models import NetSession

roster_router = APIRouter(prefix="/api/nets/{net_slug}/roster", tags=["roster"])


# --- Pydantic schemas ---


class TemplateCreate(BaseModel):
    name: str
    subject_template: str
    header_template: str
    welcome_template: str
    comments_template: str
    footer_template: str
    lead_time_days: int = 1
    is_default: bool = False


class TemplateUpdate(BaseModel):
    name: str | None = None
    subject_template: str | None = None
    header_template: str | None = None
    welcome_template: str | None = None
    comments_template: str | None = None
    footer_template: str | None = None
    lead_time_days: int | None = None
    is_default: bool | None = None


class DraftUpdate(BaseModel):
    content_subject: str | None = None
    content_header: str | None = None
    content_welcome: str | None = None
    content_comments: str | None = None
    content_footer: str | None = None


# --- Helpers ---


def _template_to_response(template) -> dict:
    return {
        "id": template.id,
        "net_id": template.net_id,
        "name": template.name,
        "subject_template": template.subject_template,
        "header_template": template.header_template,
        "welcome_template": template.welcome_template,
        "comments_template": template.comments_template,
        "footer_template": template.footer_template,
        "lead_time_days": template.lead_time_days,
        "is_default": template.is_default,
    }


def _roster_to_response(log: RosterLog) -> dict:
    return {
        "id": log.id,
        "session_id": log.session_id,
        "template_id": log.template_id,
        "status": log.status.value,
        "content_subject": log.content_subject,
        "content_header": log.content_header,
        "content_welcome": log.content_welcome,
        "content_comments": log.content_comments,
        "content_footer": log.content_footer,
        "session_url": log.session_url,
        "drafted_at": log.drafted_at.isoformat(),
        "approved_at": log.approved_at.isoformat() if log.approved_at else None,
        "sent_at": log.sent_at.isoformat() if log.sent_at else None,
        "approved_by": log.approved_by,
    }


def _verify_log_net(db: Session, log: RosterLog, net_id: int) -> bool:
    """Return True iff log's session belongs to *net_id*."""
    net_session = db.get(NetSession, log.session_id)
    if net_session is None:
        return False
    return _get_net_id_for_session(db, net_session) == net_id


# --- Template routes ---


@roster_router.post("/templates", status_code=201)
async def create_template_route(
    body: TemplateCreate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    template = create_template_service(
        db,
        net_id=ctx.net.id,
        name=body.name,
        subject_template=body.subject_template,
        header_template=body.header_template,
        welcome_template=body.welcome_template,
        comments_template=body.comments_template,
        footer_template=body.footer_template,
        lead_time_days=body.lead_time_days,
        is_default=body.is_default,
    )
    return _template_to_response(template)


@roster_router.get("/templates")
async def list_templates_route(
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    templates = list_templates_service(db, net_id=ctx.net.id)
    return [_template_to_response(t) for t in templates]


@roster_router.get("/template-defaults")
async def template_defaults_route(
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
):
    """Return the shipped seed templates so the "+ New template" UI can
    pre-fill from pristine originals, even after operators have edited
    their installed defaults."""
    from backend.modules.roster.seeds import SEED_ROSTER_TEMPLATES

    return SEED_ROSTER_TEMPLATES


@roster_router.patch("/templates/{template_id}")
async def update_template_route(
    template_id: int,
    body: TemplateUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    template = update_template_service(
        db,
        template_id,
        net_id=ctx.net.id,
        name=body.name,
        subject_template=body.subject_template,
        header_template=body.header_template,
        welcome_template=body.welcome_template,
        comments_template=body.comments_template,
        footer_template=body.footer_template,
        lead_time_days=body.lead_time_days,
        is_default=body.is_default,
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return _template_to_response(template)


@roster_router.delete("/templates/{template_id}", status_code=204)
async def delete_template_route(
    template_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    template = get_template_service(db, template_id, net_id=ctx.net.id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default template")
    delete_template_service(db, template_id, net_id=ctx.net.id)


# --- Generation routes ---


@roster_router.post("/generate/{session_id}")
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

    log = generate_draft_service(db, session_id, net_id=ctx.net.id)
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
            kind=NotificationKind.ROSTER_DRAFT,
            message=f"Roster draft ready for {_format_session_date(net_session)}",
            link_url="/roster",
            session_id=net_session.id,
        )

    return _roster_to_response(log)


@roster_router.post("/generate")
async def generate_due_drafts_route(
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    rosters = generate_due_drafts_service(db, net_id=ctx.net.id)
    return {
        "generated": len(rosters),
        "rosters": [_roster_to_response(r) for r in rosters],
    }


# --- Roster management routes ---


@roster_router.get("/")
async def list_rosters_route(
    status: str | None = Query(default=None),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.schedule.models import NetSeason

    query = (
        db.query(RosterLog)
        .join(NetSession, RosterLog.session_id == NetSession.id)
        .join(NetSeason, NetSession.season_id == NetSeason.id)
        .filter(NetSeason.net_id == ctx.net.id)
    )
    if status is not None:
        try:
            status_enum = RosterStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        query = query.filter(RosterLog.status == status_enum)
    logs = query.order_by(RosterLog.drafted_at.desc()).all()
    return [_roster_to_response(log) for log in logs]


@roster_router.get("/session/{session_id}")
async def get_roster_for_session_route(
    session_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    # Cross-net: verify session belongs to this net
    net_session = db.get(NetSession, session_id)
    if net_session is None or _get_net_id_for_session(db, net_session) != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")

    log = db.query(RosterLog).filter(RosterLog.session_id == session_id).first()
    if log is None:
        raise HTTPException(status_code=404, detail="Roster not found for session")
    return _roster_to_response(log)


@roster_router.get("/{roster_id}/preview")
async def preview_roster_route(
    roster_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    log = db.get(RosterLog, roster_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Roster not found")
    text = assemble_roster_service(db, roster_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    return {"text": text}


@roster_router.patch("/{roster_id}")
async def update_draft_route(
    roster_id: int,
    body: DraftUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = db.get(RosterLog, roster_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Roster not found")
    result = update_draft_service(
        db,
        roster_id,
        content_subject=body.content_subject,
        content_header=body.content_header,
        content_welcome=body.content_welcome,
        content_comments=body.content_comments,
        content_footer=body.content_footer,
    )
    if result is None:
        raise HTTPException(status_code=409, detail="Roster not in draft status")
    return _roster_to_response(result)


@roster_router.post("/{roster_id}/approve")
async def approve_roster_route(
    roster_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = db.get(RosterLog, roster_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Roster not found")
    result = approve_roster_service(db, roster_id, approver_callsign=ctx.user.callsign)
    if result is None:
        raise HTTPException(status_code=409, detail="Roster not in draft status")
    return _roster_to_response(result)


@roster_router.post("/{roster_id}/send")
async def mark_sent_route(
    roster_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.integrations.delivery.service import get_last_attempt_errors

    log = db.get(RosterLog, roster_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Roster not found")
    if log.status != RosterStatus.APPROVED:
        raise HTTPException(status_code=409, detail="Roster not in approved status")
    result = mark_sent_service(db, roster_id)
    if result is None:
        # mark_sent kept status APPROVED because delivery failed. Surface the
        # actual backend errors so the UI doesn't show a generic message.
        errors = get_last_attempt_errors(db, "roster", roster_id)
        detail = "Send failed: " + "; ".join(errors) if errors else "Send failed (no delivery backends configured)"
        raise HTTPException(status_code=502, detail=detail)
    return _roster_to_response(result)


@roster_router.post("/{roster_id}/resend")
async def resend_roster_route(
    roster_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.integrations.delivery.service import get_last_attempt_errors

    log = db.get(RosterLog, roster_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Roster not found")
    if log.status != RosterStatus.SENT:
        raise HTTPException(status_code=409, detail="Roster not in sent status")
    result = resend_roster_service(db, roster_id)
    if result is None:
        errors = get_last_attempt_errors(db, "roster", roster_id)
        if errors:
            detail = "Re-send failed: " + "; ".join(errors)
        else:
            detail = "Re-send failed (no delivery backends configured)"
        raise HTTPException(status_code=502, detail=detail)
    return _roster_to_response(result)


@roster_router.post("/{roster_id}/skip")
async def skip_roster_route(
    roster_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = db.get(RosterLog, roster_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Roster not found")
    result = skip_roster_service(db, roster_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Roster not in skippable status")
    return _roster_to_response(result)


@roster_router.post("/{roster_id}/regenerate")
async def regenerate_roster_route(
    roster_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = db.get(RosterLog, roster_id)
    if log is None or not _verify_log_net(db, log, ctx.net.id):
        raise HTTPException(status_code=404, detail="Roster not found")
    result = regenerate_draft_service(db, roster_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Roster not in draft status")
    return _roster_to_response(result)
