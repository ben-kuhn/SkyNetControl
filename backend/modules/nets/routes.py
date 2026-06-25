"""Net management routes.

Provides CRUD for nets, membership management, and per-net config access.
Mounted at /api/nets by backend/app.py (before per-net module routers).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import (
    NetContext,
    get_current_user,
    get_db_session,
    require_admin,
    require_net_role,
)
from backend.auth.models import User
from backend.modules.nets import service
from backend.modules.nets.config_service import set_net_config
from backend.modules.nets.models import Net, NetRole

router = APIRouter(prefix="/api/nets", tags=["nets"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class NetOut(BaseModel):
    id: int
    slug: str
    name: str
    is_public: bool

    model_config = {"from_attributes": True}


class NetIn(BaseModel):
    slug: str
    name: str


class NetPatch(BaseModel):
    slug: str | None = None
    name: str | None = None
    is_public: bool | None = None


class MemberOut(BaseModel):
    callsign: str
    name: str
    role: NetRole


class MemberIn(BaseModel):
    role: NetRole


# ---------------------------------------------------------------------------
# Net CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=list[NetOut])
def list_nets(user: User = Depends(get_current_user), db: Session = Depends(get_db_session)):
    return [NetOut.model_validate(n, from_attributes=True) for n in service.list_nets(db, user=user)]


@router.post("", response_model=NetOut, status_code=201)
def create_net(body: NetIn, admin: User = Depends(require_admin), db: Session = Depends(get_db_session)):
    try:
        net = service.create_net(db, slug=body.slug, name=body.name, creator_callsign=admin.callsign)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return NetOut.model_validate(net, from_attributes=True)


@router.get("/{net_slug}", response_model=NetOut)
def get_net(ctx: NetContext = Depends(require_net_role(NetRole.VIEWER))):
    return NetOut.model_validate(ctx.net, from_attributes=True)


@router.patch("/{net_slug}", response_model=NetOut)
def patch_net(
    body: NetPatch,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    # Admin-only fields: slug, is_public
    if (body.slug is not None or body.is_public is not None) and not ctx.user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required to change slug or visibility")
    try:
        net = service.update_net(db, net=ctx.net, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return NetOut.model_validate(net, from_attributes=True)


@router.delete("/{net_slug}", status_code=204)
def delete_net(
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    if not ctx.user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    service.delete_net(db, net=ctx.net)


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------


@router.get("/{net_slug}/members", response_model=list[MemberOut])
def list_members(
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    return service.list_memberships(db, net=ctx.net)


@router.put("/{net_slug}/members/{callsign}", response_model=MemberOut)
def put_member(
    net_slug: str,
    callsign: str,
    body: MemberIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
):
    net = db.query(Net).filter(Net.slug == net_slug).one_or_none()
    if net is None:
        raise HTTPException(status_code=404, detail="Net not found")
    try:
        m = service.add_member(db, net=net, callsign=callsign, role=body.role)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # Refresh user to get name after token_version bump
    from backend.auth.models import User as _User
    u = db.get(_User, callsign)
    return MemberOut(callsign=m.user_callsign, name=u.name if u else "", role=m.role)


@router.delete("/{net_slug}/members/{callsign}", status_code=204)
def delete_member(
    net_slug: str,
    callsign: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
):
    net = db.query(Net).filter(Net.slug == net_slug).one_or_none()
    if net is None:
        raise HTTPException(status_code=404, detail="Net not found")
    try:
        service.remove_member(db, net=net, callsign=callsign)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Per-net config
# ---------------------------------------------------------------------------


@router.get("/{net_slug}/config")
def get_config(
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    return service.list_net_config(db, net=ctx.net)


@router.put("/{net_slug}/config/{key}")
def put_config(
    key: str,
    body: dict,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    set_net_config(db, ctx.net.id, key, body["value"])
    return {"ok": True}
