from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.modules.nets.models import NetRole
from backend.integrations.winlink.models import PatConnectionSession
from backend.integrations.winlink.pat_client import PatClient, PatUnavailable
from backend.integrations.winlink.pat_config import (
    build_pat_client,
    build_pat_client as _build_pat_client,
    pat_transport_enabled,
    pat_transport_enabled_for_event,
    resolve_pat_config,
    resolve_pat_config_for_event,
)
from backend.integrations.winlink.pat_connect import build_connect_options, resolve_connect_url
from backend.integrations.winlink.pat_session import SessionBusy, engine
from backend.modules.events.event_auth import EventContext, EventRole, require_event_role

pat_router = APIRouter(prefix="/api/nets/{net_slug}", tags=["pat"])
event_pat_router = APIRouter(prefix="/api/events", tags=["events", "pat"])


class ConnectRequest(BaseModel):
    alias: str | None = None
    mode: str | None = None
    gateway: str | None = None
    freq: str | None = None


def _session_to_response(s: PatConnectionSession) -> dict:
    return {
        "id": s.id, "status": s.status.value, "method_label": s.method_label,
        "sent_count": s.sent_count, "received_count": s.received_count,
        "error": s.error, "events": s.events or [],
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
    }


def _require_enabled(db: Session, net_id: int) -> None:
    if not pat_transport_enabled(db, net_id):
        raise HTTPException(status_code=409, detail="PAT HTTP transport is not enabled for this net")


def _client(db: Session, net_id: int) -> PatClient:
    return build_pat_client(resolve_pat_config(db, net_id))


async def _do_connect(request: Request, db: Session, ctx: NetContext, body: ConnectRequest):
    _require_enabled(db, ctx.net.id)
    client = _client(db, ctx.net.id)
    try:
        aliases = client.connect_aliases()
    except PatUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"PAT unreachable: {exc}")
    try:
        url, label = resolve_connect_url(body.model_dump(exclude_none=True), aliases)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    actor = ctx.user.callsign if ctx.user else ""
    # The engine's background task outlives this request, so it must open its own
    # sessions — hand it the app-level session_factory, never the request `db`.
    session_factory = request.app.state.session_factory
    try:
        session_id = await engine.start(
            session_factory, net_id=ctx.net.id, event_id=None,
            actor=actor, connect_url=url, method_label=label, client=client,
        )
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"session_id": session_id}


@pat_router.post("/pat/connect", status_code=201)
async def net_connect_route(body: ConnectRequest, request: Request,
                            ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                            db: Session = Depends(get_db_session)):
    return await _do_connect(request, db, ctx, body)


@pat_router.get("/pat/sessions/{session_id}")
async def session_status_route(session_id: int,
                               ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                               db: Session = Depends(get_db_session)):
    s = db.get(PatConnectionSession, session_id)
    if s is None or s.net_id != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(s)


@pat_router.post("/pat/sessions/{session_id}/abort")
async def session_abort_route(session_id: int, request: Request,
                              ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                              db: Session = Depends(get_db_session)):
    s = db.get(PatConnectionSession, session_id)
    if s is None or s.net_id != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")
    await engine.abort(request.app.state.session_factory, session_id, _client(db, ctx.net.id))
    return {"ok": True}


@pat_router.get("/pat/connect-options")
async def connect_options_route(ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                                db: Session = Depends(get_db_session)):
    _require_enabled(db, ctx.net.id)
    try:
        return build_connect_options(_client(db, ctx.net.id))
    except PatUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"PAT unreachable: {exc}")


def _probe_status(client: PatClient) -> bool:
    client.status()
    return True


@pat_router.post("/pat/test")
async def test_route(ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                     db: Session = Depends(get_db_session)):
    cfg = resolve_pat_config(db, ctx.net.id)
    if not cfg.base_url:
        return {"ok": False, "error": "No PAT base URL configured"}
    try:
        _probe_status(build_pat_client(cfg))
        return {"ok": True}
    except PatUnavailable as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Event-scoped PAT routes — /api/events/{event_id}/pat/…
# ---------------------------------------------------------------------------


def _require_event_pat_enabled(db: Session, event_id: int) -> None:
    if not pat_transport_enabled_for_event(db, event_id):
        raise HTTPException(status_code=409, detail="PAT HTTP transport is not enabled for this event")


def _event_client(db: Session, event_id: int) -> PatClient:
    return _build_pat_client(resolve_pat_config_for_event(db, event_id))


@event_pat_router.post("/{event_id}/pat/connect", status_code=201)
async def event_pat_connect_route(
    body: ConnectRequest,
    request: Request,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    _require_event_pat_enabled(db, ctx.event.id)
    client = _event_client(db, ctx.event.id)
    try:
        aliases = client.connect_aliases()
    except PatUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"PAT unreachable: {exc}")
    try:
        url, label = resolve_connect_url(body.model_dump(exclude_none=True), aliases)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    actor = ctx.user.callsign if ctx.user else ""
    session_factory = request.app.state.session_factory
    try:
        session_id = await engine.start(
            session_factory,
            net_id=None,
            event_id=ctx.event.id,
            actor=actor,
            connect_url=url,
            method_label=label,
            client=client,
        )
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"session_id": session_id}


@event_pat_router.get("/{event_id}/pat/sessions/{session_id}")
async def event_pat_session_status_route(
    session_id: int,
    ctx: EventContext = Depends(require_event_role(EventRole.READ)),
    db: Session = Depends(get_db_session),
):
    s = db.get(PatConnectionSession, session_id)
    if s is None or s.event_id != ctx.event.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(s)


@event_pat_router.post("/{event_id}/pat/sessions/{session_id}/abort")
async def event_pat_abort_route(
    session_id: int,
    request: Request,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    s = db.get(PatConnectionSession, session_id)
    if s is None or s.event_id != ctx.event.id:
        raise HTTPException(status_code=404, detail="Session not found")
    await engine.abort(request.app.state.session_factory, session_id, _event_client(db, ctx.event.id))
    return {"ok": True}


@event_pat_router.get("/{event_id}/pat/connect-options")
async def event_pat_connect_options_route(
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    _require_event_pat_enabled(db, ctx.event.id)
    try:
        return build_connect_options(_event_client(db, ctx.event.id))
    except PatUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"PAT unreachable: {exc}")


@event_pat_router.post("/{event_id}/pat/test")
async def event_pat_test_route(
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    cfg = resolve_pat_config_for_event(db, ctx.event.id)
    if not cfg.base_url:
        return {"ok": False, "error": "No PAT base URL configured"}
    try:
        _probe_status(_build_pat_client(cfg))
        return {"ok": True}
    except PatUnavailable as exc:
        return {"ok": False, "error": str(exc)}
