from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.config_mgmt.service import get_config_value
from backend.modules.forms.catalog import build_catalog
from backend.modules.nets.models import NetRole

net_forms_router = APIRouter(prefix="/api/nets/{net_slug}/forms", tags=["forms"])


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
