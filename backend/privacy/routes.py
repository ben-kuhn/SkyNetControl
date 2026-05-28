import json

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.privacy.service import anonymize_user, export_user_data

privacy_router = APIRouter()


class AnonymizeRequest(BaseModel):
    confirm: bool = False


@privacy_router.get("/export")
def export_own_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    data = export_user_data(db, user.callsign)
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="skynetcontrol-export-{user.callsign}.json"'},
    )


@privacy_router.get("/export/{callsign}")
def export_user_data_admin(
    callsign: str,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    try:
        data = export_user_data(db, callsign)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="skynetcontrol-export-{callsign}.json"'},
    )


@privacy_router.post("/anonymize")
def anonymize_own_account(
    body: AnonymizeRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")

    try:
        result = anonymize_user(db, user.callsign, actor_callsign=user.callsign)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")

    return {
        "anonymized": True,
        "anonymous_id": result["anonymous_id"],
        "message": "Account anonymized. All personal data has been replaced.",
    }


@privacy_router.post("/anonymize/{callsign}")
def anonymize_user_admin(
    callsign: str,
    body: AnonymizeRequest,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")

    try:
        result = anonymize_user(db, callsign, actor_callsign=user.callsign)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "anonymized": True,
        "anonymous_id": result["anonymous_id"],
        "message": "Account anonymized. All personal data has been replaced.",
    }
