from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, get_current_user, require_role
from backend.auth.models import User, UserRole
from backend.config_mgmt.service import get_all_config, set_config_value

config_router = APIRouter(tags=["config"])


class ConfigValueRequest(BaseModel):
    value: str


@config_router.get("/")
async def list_config(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    return get_all_config(db)


@config_router.put("/{key}")
async def update_config(
    key: str,
    body: ConfigValueRequest,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    set_config_value(db, key, body.value)
    return {"key": key, "value": body.value}
