"""Event-scoped forms catalog + render — same on-disk templates as the net forms
routes (backend/modules/forms/net_routes.py), CONTROL-gated by event."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session
from backend.config_mgmt.service import get_config_value
from backend.modules.events.event_auth import EventContext, EventRole, require_event_role
from backend.modules.forms.catalog import build_catalog
from backend.modules.forms.serve import render_input_form

event_forms_router = APIRouter(prefix="/api/events", tags=["events", "forms"])

_SANDBOX_CSP = (
    "sandbox; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'"
)


def _filter_tree(node: dict, q: str) -> dict:
    forms = [f for f in node["forms"] if q in f["name"].lower()]
    folders = [_filter_tree(sub, q) for sub in node["folders"]]
    folders = [f for f in folders if f["forms"] or f["folders"]]
    return {"name": node["name"], "folders": folders, "forms": forms}


@event_forms_router.get("/{event_id}/forms/catalog")
async def event_forms_catalog_route(
    q: str = Query(default=""),
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    version = get_config_value(db, "forms.library_version", "") or ""
    tree = build_catalog(version)
    if q:
        tree = _filter_tree(tree, q.strip().lower())
    return tree


@event_forms_router.get("/{event_id}/forms/render")
async def event_forms_render_route(
    path: str = Query(...),
    prefill: str = Query(default=""),
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    prefill_dict: dict = {}
    if prefill:
        try:
            parsed = json.loads(prefill)
            if isinstance(parsed, dict):
                prefill_dict = parsed
        except (json.JSONDecodeError, ValueError):
            pass  # malformed prefill: treat as empty, do not 500
    try:
        html = render_input_form(path, prefill=prefill_dict)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Form not found")
    return HTMLResponse(content=html, headers={"Content-Security-Policy": _SANDBOX_CSP})
