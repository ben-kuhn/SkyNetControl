"""HTTP routes for the admin recovery flow.

GET  /api/recovery/status — reports whether any outstanding (unused, unexpired)
                            tokens exist, so the frontend knows whether to show
                            the token-entry form.
POST /api/recovery/claim  — validates a plaintext token, marks it used, and
                            issues a short-lived recovery cookie (JWT signed with
                            the same SKYNET_JWT_SECRET_KEY as user sessions but
                            with a distinct ``type: "recovery"`` claim).
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, get_settings
from backend.auth.rate_limit import rate_limit
from backend.auth.recovery import (
    claim_token,
    cookie_ttl_seconds,
    list_outstanding,
    make_recovery_token,
)
from backend.config import Settings

recovery_router = APIRouter(prefix="/recovery", tags=["recovery"])


@recovery_router.get(
    "/status",
    # Public info disclosure (boolean). Keep the rate generous; this is
    # polled by the recovery UI.
    dependencies=[Depends(rate_limit("recovery.status", capacity=30, refill_per_sec=30 / 60))],
)
def recovery_status(db: Session = Depends(get_db_session)) -> dict:
    """Return {outstanding: bool} — true iff at least one unused, unexpired token exists."""
    return {"outstanding": len(list_outstanding(db)) > 0}


class RecoveryClaimRequest(BaseModel):
    token: str


@recovery_router.post(
    "/claim",
    # Tight: this is the brute-force surface. Plaintext recovery tokens
    # are 256-bit random so guessing is infeasible — but rate limiting
    # still suppresses any sustained probing.
    dependencies=[Depends(rate_limit("recovery.claim", capacity=5, refill_per_sec=5 / 60))],
)
def recovery_claim(
    body: RecoveryClaimRequest,
    response: Response,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Validate token, mark used, set recovery cookie, return 200.

    Returns 400 if the token is invalid, already used, or expired.

    Atomicity: `claim_token` does the verify + mark-used in a single
    UPDATE so two simultaneous claims of the same token can't both win.
    """
    row = claim_token(db, body.token)
    if row is None:
        raise HTTPException(status_code=400, detail="invalid, used, or expired token")
    cookie_value = make_recovery_token(hash_prefix=row.token_hash[:8], settings=settings)
    is_secure = settings.app_base_url.startswith("https://")
    response.set_cookie(
        key="recovery_token",
        value=cookie_value,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=cookie_ttl_seconds(),
    )
    return {"ok": True}


@recovery_router.post("/logout")
def recovery_logout(response: Response) -> dict:
    """Clear the recovery_token cookie.

    The cookie is HttpOnly, so the frontend can't clear it from JavaScript —
    it has to ask the server to send a max_age=0 Set-Cookie header back.
    """
    response.delete_cookie(key="recovery_token", path="/")
    return {"ok": True}
