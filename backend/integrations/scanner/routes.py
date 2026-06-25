from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, require_net_member
from backend.integrations.scanner.service import run_scan, scanner_state

scanner_router = APIRouter()


@scanner_router.get("/status")
def get_scanner_status(
    _user=Depends(require_net_member),
):
    next_scan_time = None
    if scanner_state.running and scanner_state.last_scan_time:
        next_scan_time = (scanner_state.last_scan_time + timedelta(minutes=scanner_state.interval_minutes)).isoformat()

    return {
        "running": scanner_state.running,
        "last_scan_time": scanner_state.last_scan_time.isoformat() if scanner_state.last_scan_time else None,
        "next_scan_time": next_scan_time,
        "last_scan_count": scanner_state.last_scan_count,
        "active_session_id": scanner_state.active_session_id,
    }


@scanner_router.post("/trigger")
def trigger_scan(
    db: Session = Depends(get_db_session),
    _user=Depends(require_net_member),
):
    now = datetime.now(tz=timezone.utc)
    count = run_scan(db, now)

    if count is None:
        return {"imported": None, "message": "Scan skipped — no active session or config missing"}

    return {"imported": count, "message": f"Scan complete, {count} check-ins imported"}
