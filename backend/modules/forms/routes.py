"""Admin endpoints for managing the Winlink Standard Forms library."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, require_role
from backend.auth.models import UserRole
from backend.config_mgmt.service import get_config_value, set_config_value
from backend.modules.forms.fetch import (
    DEFAULT_SOURCE_URL,
    FormsFetchError,
    fetch_and_install,
)

forms_router = APIRouter(prefix="/api/config/forms", tags=["forms"])


@forms_router.get("/status")
async def get_forms_status(
    db: Session = Depends(get_db_session),
    _user=Depends(require_role(UserRole.ADMIN)),
) -> dict:
    return {
        "source_url": get_config_value(db, "forms.source_url") or DEFAULT_SOURCE_URL,
        "library_version": get_config_value(db, "forms.library_version"),
        "last_fetched_at": get_config_value(db, "forms.last_fetched_at"),
    }


@forms_router.post("/fetch")
async def fetch_forms_library(
    db: Session = Depends(get_db_session),
    _user=Depends(require_role(UserRole.ADMIN)),
) -> dict:
    source_url = get_config_value(db, "forms.source_url") or DEFAULT_SOURCE_URL
    try:
        result = await fetch_and_install(source_url)
    except FormsFetchError as exc:
        # Validation / extraction failures are 400 (operator's mistake or
        # upstream serving garbage); SSRF guard failures bubble up here too.
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        # Network errors, timeouts: 502.
        raise HTTPException(status_code=502, detail=f"failed to fetch forms library: {exc}")

    set_config_value(db, "forms.library_version", result["library_version"])
    set_config_value(db, "forms.last_fetched_at", result["last_fetched_at"])

    return result
