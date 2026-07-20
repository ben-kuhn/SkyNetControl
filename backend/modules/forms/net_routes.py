from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.config_mgmt.service import get_config_value
from backend.modules.forms.catalog import build_catalog
from backend.modules.forms.serve import render_input_form
from backend.modules.nets.models import NetRole

net_forms_router = APIRouter(prefix="/api/nets/{net_slug}/forms", tags=["forms"])

_SANDBOX_CSP = (
    "sandbox; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'"
)


@net_forms_router.get("/render")
async def forms_render_route(
    path: str = Query(...),
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
):
    try:
        html = render_input_form(path)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Form not found")
    return HTMLResponse(content=html, headers={"Content-Security-Policy": _SANDBOX_CSP})


def _filter_tree(node: dict, q: str) -> dict:
    """Return a copy keeping only forms whose name contains q (case-insensitive)
    and folders that still have content."""
    forms = [f for f in node["forms"] if q in f["name"].lower()]
    folders = [_filter_tree(sub, q) for sub in node["folders"]]
    folders = [f for f in folders if f["forms"] or f["folders"]]
    return {"name": node["name"], "folders": folders, "forms": forms}


@net_forms_router.get("/catalog")
async def forms_catalog_route(
    q: str = Query(default=""),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    version = get_config_value(db, "forms.library_version", "") or ""
    tree = build_catalog(version)
    if q:
        tree = _filter_tree(tree, q.strip().lower())
    return tree
