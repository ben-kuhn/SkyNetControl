from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.integrations.scanner.service import run_scan, scan_one, scanner_state
from backend.modules.nets.models import NetRole

scanner_router = APIRouter()


@scanner_router.get("/status")
def get_scanner_status(
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    next_scan_time = None
    if scanner_state.running and scanner_state.last_scan_time:
        next_scan_time = (scanner_state.last_scan_time + timedelta(minutes=scanner_state.interval_minutes)).isoformat()

    # scanner_state is process-global; expose active_session_id only when it
    # belongs to the requesting net, otherwise other tenants' session IDs leak.
    active_session_id: int | None = None
    if scanner_state.active_session_id is not None:
        from backend.modules.checkins.service import get_net_id_for_session
        from backend.modules.schedule.models import NetSession

        sess = db.get(NetSession, scanner_state.active_session_id)
        if sess is not None and get_net_id_for_session(db, sess) == ctx.net.id:
            active_session_id = scanner_state.active_session_id

    return {
        "running": scanner_state.running,
        "last_scan_time": scanner_state.last_scan_time.isoformat() if scanner_state.last_scan_time else None,
        "next_scan_time": next_scan_time,
        "last_scan_count": scanner_state.last_scan_count,
        "active_session_id": active_session_id,
    }


@scanner_router.post("/trigger")
def trigger_scan(
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.nets.config_service import get_net_config

    now = datetime.now(tz=timezone.utc)

    # Use per-net mailbox config if available, else fall back to global config
    mailbox = get_net_config(db, ctx.net.id, "pat_mailbox_path")
    if mailbox:
        count = scan_one(db, ctx.net.id, mailbox, now)
    else:
        # Fallback: global config (backward compat during migration)
        count = run_scan(db, now)

    if count is None:
        return {"imported": None, "message": "Scan skipped — no active session or config missing"}

    return {"imported": count, "message": f"Scan complete, {count} check-ins imported"}
