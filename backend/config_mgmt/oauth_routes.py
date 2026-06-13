from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.audit.service import log_action
from backend.auth.dependencies import get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.auth.oidc_slug import slugify, validate_slug
from backend.config_mgmt.oauth import (
    OAuthProviderConfig,
    delete_oauth_provider,
    get_oauth_provider,
    list_oauth_providers,
    upsert_oauth_provider,
)

oauth_router = APIRouter(prefix="/oauth/providers", tags=["admin-oauth"])

_REDACTED = "***"


class OAuthProviderResponse(BaseModel):
    slug: str
    name: str
    enabled: bool
    client_id: str
    client_secret: str  # always "***" or "" — never the real secret
    issuer_url: str


class OAuthProviderUpsert(BaseModel):
    name: str
    enabled: bool
    client_id: str
    client_secret: str  # "" = preserve existing; "-" = clear; anything else = set
    issuer_url: str


def _to_response(p: OAuthProviderConfig) -> OAuthProviderResponse:
    return OAuthProviderResponse(
        slug=p.slug,
        name=p.name,
        enabled=p.enabled,
        client_id=p.client_id,
        client_secret=_REDACTED if p.client_secret else "",
        issuer_url=p.issuer_url,
    )


@oauth_router.get("")
def list_providers(
    _: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> list[OAuthProviderResponse]:
    return [_to_response(p) for p in list_oauth_providers(db)]


@oauth_router.get("/{slug}")
def get_provider(
    slug: str,
    _: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> OAuthProviderResponse:
    p = get_oauth_provider(db, slug)
    if p is None:
        raise HTTPException(404)
    return _to_response(p)


@oauth_router.put("/{slug}")
def upsert_provider(
    slug: str,
    body: OAuthProviderUpsert,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> OAuthProviderResponse:
    existing = get_oauth_provider(db, slug)
    if body.client_secret == "":
        # "" means "preserve existing"; on a fresh create that's an error
        # — saving a provider with no secret would silently break OAuth.
        if existing is None:
            raise HTTPException(400, detail="client_secret is required when creating a provider")
        secret = existing.client_secret
    elif body.client_secret == "-":
        secret = ""
    else:
        secret = body.client_secret
    provider = OAuthProviderConfig(
        slug=slug,
        name=body.name,
        enabled=body.enabled,
        client_id=body.client_id,
        client_secret=secret,
        issuer_url=body.issuer_url,
    )
    try:
        upsert_oauth_provider(db, provider)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    log_action(
        db,
        actor=user.callsign,
        action="oauth.provider.upserted",
        details={
            "slug": slug,
            "name": body.name,
            "enabled": body.enabled,
            "client_id": body.client_id,
            "issuer_url": body.issuer_url,
            "secret_changed": body.client_secret not in ("", _REDACTED),
        },
    )
    return _to_response(provider)


@oauth_router.delete("/{slug}", status_code=204)
def delete_provider(
    slug: str,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> None:
    delete_oauth_provider(db, slug)
    log_action(
        db,
        actor=user.callsign,
        action="oauth.provider.deleted",
        details={"slug": slug},
    )


@oauth_router.post("/slug/derive")
def derive_slug(
    name: str,
    _: User = Depends(require_role(UserRole.ADMIN)),
) -> dict:
    slug = slugify(name)
    err = validate_slug(slug)
    if err is not None:
        return {"slug": slug, "valid": False, "error": err}
    return {"slug": slug, "valid": True}
