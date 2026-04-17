from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.modules.activities.models import Activity, ActivityTag
from backend.modules.activities.service import (
    create_activity,
    delete_activity,
    get_activity,
    list_activities,
    update_activity,
)

activities_router = APIRouter(tags=["activities"])


# --- Pydantic schemas ---


class ActivityCreate(BaseModel):
    title: str
    description: str
    instructions: str
    tag_names: list[str] = []


class ActivityUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    instructions: str | None = None
    tag_names: list[str] | None = None


class TagResponse(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


# --- Helpers ---


def _activity_to_response(activity: Activity) -> dict:
    return {
        "id": activity.id,
        "title": activity.title,
        "description": activity.description,
        "instructions": activity.instructions,
        "is_default": activity.is_default,
        "created_at": activity.created_at.isoformat(),
        "last_used_at": activity.last_used_at.isoformat() if activity.last_used_at else None,
        "tags": [{"id": t.id, "name": t.name} for t in activity.tags],
    }


# --- Routes ---


@activities_router.post("/", status_code=201)
async def create_activity_route(
    body: ActivityCreate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    activity = create_activity(
        db,
        title=body.title,
        description=body.description,
        instructions=body.instructions,
        tag_names=body.tag_names,
    )
    return _activity_to_response(activity)


@activities_router.get("/")
async def list_activities_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    activities = list_activities(db)
    return [_activity_to_response(a) for a in activities]


@activities_router.get("/tags")
async def list_tags_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    tags = db.query(ActivityTag).order_by(ActivityTag.name).all()
    return [{"id": t.id, "name": t.name} for t in tags]


@activities_router.get("/{activity_id}")
async def get_activity_route(
    activity_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    activity = get_activity(db, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return _activity_to_response(activity)


@activities_router.patch("/{activity_id}")
async def update_activity_route(
    activity_id: int,
    body: ActivityUpdate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    activity = update_activity(
        db,
        activity_id,
        title=body.title,
        description=body.description,
        instructions=body.instructions,
        tag_names=body.tag_names,
    )
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return _activity_to_response(activity)


@activities_router.delete("/{activity_id}", status_code=204)
async def delete_activity_route(
    activity_id: int,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    activity = get_activity(db, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    if activity.is_default:
        raise HTTPException(status_code=403, detail="Cannot delete the default activity")
    delete_activity(db, activity_id)
