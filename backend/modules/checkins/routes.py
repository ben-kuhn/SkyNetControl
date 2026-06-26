import os

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import (
    NetContext,
    get_db_session,
    require_net_role,
)
from backend.config_mgmt.service import get_config_value, get_checkin_modes
from backend.modules.checkins.mailbox_reader import read_mailbox
from backend.modules.checkins.models import (
    CheckIn,
    Member,
    MessageType,
    ParseStatus,
)
from backend.modules.checkins.service import (
    approve_session_checkins,
    create_manual_checkin,
    delete_checkin,
    get_checkins_by_callsign,
    get_checkins_for_session,
    get_net_id_for_session,
    reparse_checkin,
    reparse_session,
    scan_and_import_messages,
    update_checkin,
)
from backend.integrations.callbook.service import is_callbook_configured, lookup_callsign
from backend.modules.nets.models import NetRole
from backend.modules.schedule.models import NetSession

# TODO(Task 13): replace DEFAULT_NET_SLUG with CurrentNetContext once available.
checkins_router = APIRouter(prefix="/api/nets/{net_slug}/checkins", tags=["checkins"])


class ManualCheckinCreate(BaseModel):
    session_id: int
    callsign: str
    name: str
    mode: str
    city: str | None = None
    county: str | None = None
    state: str | None = None
    comments: str | None = None


class CheckinUpdate(BaseModel):
    name: str | None = None
    callsign: str | None = None
    city: str | None = None
    county: str | None = None
    state: str | None = None
    mode: str | None = None
    comments: str | None = None
    parse_status: ParseStatus | None = None


def _render_winlink_form_view(body: str) -> str | None:
    """Best-effort render a winlink form body. Never raises."""
    import xml.etree.ElementTree as ET
    from backend.modules.checkins.message_parser import extract_form_variables, extract_form_xml
    from backend.modules.forms.render import render_form_view

    xml_chunk = extract_form_xml(body) or body
    try:
        root = ET.fromstring(xml_chunk)
    except ET.ParseError:
        return None
    template_filename = ""
    df = root.find(".//form_parameters/display_form")
    if df is not None and df.text:
        template_filename = df.text.strip()
    return render_form_view(template_filename, extract_form_variables(root))


def _checkin_to_response(checkin: CheckIn) -> dict:
    raw = checkin.raw_message
    raw_payload: dict | None
    form_view_html: str | None = None
    if raw is None:
        raw_payload = None
    else:
        raw_payload = {
            "subject": raw.subject,
            "from_address": raw.from_address,
            "received_at": raw.received_at.isoformat(),
            "body": raw.body,
            "message_type": raw.message_type.value,
        }
        if raw.message_type == MessageType.WINLINK_FORM:
            # Note: spec called for lazy rendering on the list endpoint; at this scale
            # (dozens of check-ins per session, no pagination) we compute eagerly.
            form_view_html = _render_winlink_form_view(raw.body)

    return {
        "id": checkin.id,
        "session_id": checkin.session_id,
        "raw_message_id": checkin.raw_message_id,
        "callsign": checkin.callsign,
        "name": checkin.name,
        "city": checkin.city,
        "county": checkin.county,
        "state": checkin.state,
        "mode": checkin.mode,
        "comments": checkin.comments,
        "latitude": checkin.latitude,
        "longitude": checkin.longitude,
        "parse_status": checkin.parse_status.value,
        "timing_status": checkin.timing_status.value,
        "is_new_member": checkin.is_new_member,
        "raw_message": raw_payload,
        "form_view_html": form_view_html,
    }


def _member_to_response(member: Member) -> dict:
    return {
        "callsign": member.callsign,
        "name": member.name,
        "first_check_in_date": member.first_check_in_date.isoformat(),
        "last_check_in_date": member.last_check_in_date.isoformat(),
        "total_check_ins": member.total_check_ins,
    }


def _checkin_to_response_with_session(checkin: CheckIn, session_date) -> dict:
    base = _checkin_to_response(checkin)
    base["session_date"] = session_date.isoformat()
    return base


def _get_net_config_value(db: Session, net_id: int, key: str, default: str = "") -> str:
    """Read per-net config, fall back to global app_config for backward compat."""
    from backend.modules.nets.config_service import get_net_config
    val = get_net_config(db, net_id, key)
    if val is not None:
        return val
    return get_config_value(db, key, default)


@checkins_router.get("/modes")
async def get_modes_route(
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    return get_checkin_modes(db)


@checkins_router.post("/scan/{session_id}")
async def scan_mailbox_route(
    session_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    net_session = db.get(NetSession, session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Cross-net isolation: ensure session belongs to this net
    session_net_id = get_net_id_for_session(db, net_session)
    if session_net_id != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")

    mailbox_path = _get_net_config_value(db, ctx.net.id, "pat_mailbox_path")
    net_address = _get_net_config_value(db, ctx.net.id, "net_address")
    if not mailbox_path or not net_address:
        raise HTTPException(
            status_code=503,
            detail="PAT mailbox path or net address not configured",
        )

    # PAT stores incoming messages in {mailbox_path}/in — match the
    # background scanner's behavior so both paths see the same files.
    inbox_path = os.path.join(mailbox_path, "in")
    raw_messages = read_mailbox(inbox_path, net_address=net_address)
    checkins = scan_and_import_messages(db, raw_messages, net_session, net_id=ctx.net.id)

    return {
        "imported": len(checkins),
        "checkins": [_checkin_to_response(c) for c in checkins],
    }


@checkins_router.get("/session/{session_id}")
async def get_session_checkins_route(
    session_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    net_session = db.get(NetSession, session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Cross-net isolation
    session_net_id = get_net_id_for_session(db, net_session)
    if session_net_id != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")

    checkins = get_checkins_for_session(db, session_id)
    return [_checkin_to_response(c) for c in checkins]


@checkins_router.post("/manual", status_code=201)
async def create_manual_checkin_route(
    body: ManualCheckinCreate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    net_session = db.get(NetSession, body.session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Cross-net isolation
    session_net_id = get_net_id_for_session(db, net_session)
    if session_net_id != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")

    checkin = create_manual_checkin(
        db,
        session_id=body.session_id,
        callsign=body.callsign,
        name=body.name,
        mode=body.mode,
        net_id=ctx.net.id,
        city=body.city,
        county=body.county,
        state=body.state,
        comments=body.comments,
    )
    return _checkin_to_response(checkin)


@checkins_router.patch("/{checkin_id}")
async def update_checkin_route(
    checkin_id: int,
    body: CheckinUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    checkin = update_checkin(
        db,
        checkin_id,
        name=body.name,
        callsign=body.callsign,
        city=body.city,
        county=body.county,
        state=body.state,
        mode=body.mode,
        comments=body.comments,
        parse_status=body.parse_status,
        net_id=ctx.net.id,
    )
    if checkin is None:
        raise HTTPException(status_code=404, detail="Check-in not found")
    return _checkin_to_response(checkin)


@checkins_router.delete("/{checkin_id}", status_code=204)
async def delete_checkin_route(
    checkin_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    if not delete_checkin(db, checkin_id, net_id=ctx.net.id):
        raise HTTPException(status_code=404, detail="Check-in not found")
    return Response(status_code=204)


@checkins_router.post("/{checkin_id}/reparse")
async def reparse_checkin_route(
    checkin_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    """Re-run the parser against this check-in's stored raw message.

    404 if the check-in is missing or was manually entered (no raw message
    to re-parse against).
    """
    # Cross-net isolation: confirm the check-in's session belongs to this net.
    existing = db.get(CheckIn, checkin_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Check-in not found")
    session_obj = db.get(NetSession, existing.session_id)
    if session_obj is None or get_net_id_for_session(db, session_obj) != ctx.net.id:
        raise HTTPException(status_code=404, detail="Check-in not found")

    checkin = reparse_checkin(db, checkin_id)
    if checkin is None:
        raise HTTPException(status_code=404, detail="Check-in has no raw message to re-parse")
    return _checkin_to_response(checkin)


@checkins_router.post("/session/{session_id}/reparse")
async def reparse_session_route(
    session_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    """Re-parse every existing check-in for the session and reclaim any
    orphan RawMessages whose ``received_at`` falls inside the session window.

    Use case: parser fix deployed, or a check-in was deleted in error.
    """
    net_session = db.get(NetSession, session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if get_net_id_for_session(db, net_session) != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return reparse_session(db, net_session)


@checkins_router.post("/approve/{session_id}")
async def approve_session_route(
    session_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    net_session = db.get(NetSession, session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Cross-net isolation
    session_net_id = get_net_id_for_session(db, net_session)
    if session_net_id != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")

    checkins = get_checkins_for_session(db, session_id)
    approve_session_checkins(db, session_id, net_id=ctx.net.id)

    db.refresh(net_session)
    return {
        "session_status": net_session.status.value,
        "members_updated": len(checkins),
    }


@checkins_router.get("/members")
async def list_members_route(
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    members = db.query(Member).filter(Member.net_id == ctx.net.id).order_by(Member.callsign).all()
    return [_member_to_response(m) for m in members]


@checkins_router.get("/by-callsign/{callsign}")
async def get_checkins_by_callsign_route(
    callsign: str,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    rows = get_checkins_by_callsign(db, callsign, net_id=ctx.net.id)
    return [_checkin_to_response_with_session(c, d) for c, d in rows]


@checkins_router.get("/lookup/{callsign}")
async def lookup_callsign_route(
    callsign: str,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    if not is_callbook_configured(db):
        raise HTTPException(
            status_code=503,
            detail="Callbook lookup not configured — set a provider and credentials on the Config page.",
        )
    result = lookup_callsign(db, callsign)
    if result is None:
        raise HTTPException(status_code=404, detail="Callsign not found in configured callbooks")
    return result
