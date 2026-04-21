from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.config_mgmt.service import get_config_value
from backend.modules.activities.chat_service import (
    create_chat_session,
    get_chat_history,
    get_chat_session,
    link_chat_to_activity,
    send_message,
)
from backend.modules.activities.models import Activity, ActivityTag, ChatMessage, ChatSession
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


# --- Chat schemas ---


class ChatMessageRequest(BaseModel):
    content: str


class ChatApproveRequest(BaseModel):
    title: str
    description: str
    instructions: str
    tag_names: list[str] = []


# --- Chat helpers ---


def _chat_session_to_response(chat: ChatSession, messages: list[ChatMessage] | None = None) -> dict:
    msgs = messages if messages is not None else (chat.messages if chat.messages else [])
    return {
        "id": chat.id,
        "activity_id": chat.activity_id,
        "created_at": chat.created_at.isoformat(),
        "messages": [
            {
                "id": m.id,
                "role": m.role.value,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
    }


def _message_to_response(msg: ChatMessage) -> dict:
    return {
        "id": msg.id,
        "role": msg.role.value,
        "content": msg.content,
        "created_at": msg.created_at.isoformat(),
    }


# --- Chat routes ---


@activities_router.post("/chat/sessions", status_code=201)
async def create_chat_session_route(
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    chat = create_chat_session(db)
    return _chat_session_to_response(chat)


@activities_router.get("/chat/sessions/{chat_session_id}")
async def get_chat_session_route(
    chat_session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    chat = get_chat_session(db, chat_session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    messages = get_chat_history(db, chat_session_id)
    return _chat_session_to_response(chat, messages)


@activities_router.post("/chat/sessions/{chat_session_id}/messages")
async def send_chat_message_route(
    chat_session_id: int,
    body: ChatMessageRequest,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    chat = get_chat_session(db, chat_session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    api_key = get_config_value(db, "claude_api_key")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Claude API key not configured. Set 'claude_api_key' in app config.",
        )

    try:
        user_msg, assistant_msg = send_message(db, chat_session_id, body.content, api_key=api_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
    return {
        "user_message": _message_to_response(user_msg),
        "assistant_message": _message_to_response(assistant_msg),
    }


@activities_router.post("/chat/sessions/{chat_session_id}/approve", status_code=201)
async def approve_chat_route(
    chat_session_id: int,
    body: ChatApproveRequest,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    chat = get_chat_session(db, chat_session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    activity = create_activity(
        db,
        title=body.title,
        description=body.description,
        instructions=body.instructions,
        tag_names=body.tag_names,
    )
    link_chat_to_activity(db, chat_session_id, activity.id)
    return _activity_to_response(activity)
