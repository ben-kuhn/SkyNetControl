import json
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from backend.config_mgmt.service import get_config_value
from backend.integrations.callbook.models import CallbookCache
from backend.integrations.callbook.providers import (
    CallbookResult,
    HamQTHProvider,
    QRZProvider,
)

CACHE_TTL_DAYS = 30

_session_tokens: dict[str, str] = {}

_PROVIDERS = {
    "hamqth": HamQTHProvider,
    "qrz": QRZProvider,
}


def _lookup_from_provider(
    provider,
    callsign: str,
    username: str,
    password: str,
    provider_name: str,
) -> CallbookResult | None:
    token = _session_tokens.get(provider_name)

    if token:
        result = provider.lookup(callsign, token)
        if result is not None:
            return result

    try:
        token = provider.authenticate(username, password)
        _session_tokens[provider_name] = token
    except Exception:
        return None

    return provider.lookup(callsign, token)


def _cache_to_dict(entry: CallbookCache, cached: bool) -> dict:
    return {
        "callsign": entry.callsign,
        "name": entry.name,
        "city": entry.city,
        "county": entry.county,
        "state": entry.state,
        "country": entry.country,
        "latitude": entry.latitude,
        "longitude": entry.longitude,
        "source": entry.source,
        "cached": cached,
    }


def _result_to_dict(result: CallbookResult) -> dict:
    return {
        "callsign": result.callsign,
        "name": result.name,
        "city": result.city,
        "county": result.county,
        "state": result.state,
        "country": result.country,
        "latitude": result.latitude,
        "longitude": result.longitude,
        "source": result.source,
        "cached": False,
    }


def _update_cache(db: Session, result: CallbookResult) -> None:
    existing = db.get(CallbookCache, result.callsign)
    if existing:
        existing.name = result.name
        existing.city = result.city
        existing.county = result.county
        existing.state = result.state
        existing.country = result.country
        existing.latitude = result.latitude
        existing.longitude = result.longitude
        existing.source = result.source
        existing.fetched_at = datetime.now(timezone.utc)
    else:
        db.add(
            CallbookCache(
                callsign=result.callsign,
                name=result.name,
                city=result.city,
                county=result.county,
                state=result.state,
                country=result.country,
                latitude=result.latitude,
                longitude=result.longitude,
                source=result.source,
                fetched_at=datetime.now(timezone.utc),
            )
        )
    db.commit()


def is_callbook_configured(db: Session) -> bool:
    """True iff at least one provider is enabled with both username and password.

    Lets the route distinguish 'no callbook configured' (503) from 'callsign
    not in any callbook' (404) — operator gets an actionable error instead
    of the opaque 'not found' both used to produce (backlog item 4).
    """
    providers_json = get_config_value(db, "callbook.providers")
    if not providers_json:
        return False
    try:
        provider_names = json.loads(providers_json)
    except (json.JSONDecodeError, TypeError):
        return False
    for name in provider_names:
        if name not in _PROVIDERS:
            continue
        username = get_config_value(db, f"callbook.{name}.username", "")
        password = get_config_value(db, f"callbook.{name}.password", "")
        if username and password:
            return True
    return False


def lookup_callsign(db: Session, callsign: str) -> dict | None:
    callsign = callsign.upper()

    cached = db.get(CallbookCache, callsign)
    if cached:
        age = datetime.now(timezone.utc) - cached.fetched_at.replace(tzinfo=timezone.utc)
        if age < timedelta(days=CACHE_TTL_DAYS):
            return _cache_to_dict(cached, cached=True)

    providers_json = get_config_value(db, "callbook.providers")
    if not providers_json:
        return None

    try:
        provider_names = json.loads(providers_json)
    except (json.JSONDecodeError, TypeError):
        return None

    for name in provider_names:
        provider_cls = _PROVIDERS.get(name)
        if provider_cls is None:
            continue

        username = get_config_value(db, f"callbook.{name}.username", "")
        password = get_config_value(db, f"callbook.{name}.password", "")
        if not username or not password:
            continue

        provider = provider_cls()
        result = _lookup_from_provider(provider, callsign, username, password, name)
        if result is not None:
            _update_cache(db, result)
            return _result_to_dict(result)

    return None
