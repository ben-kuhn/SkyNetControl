"""City/state -> lat/lon via OpenStreetMap Nominatim, with a DB cache.

Nominatim's ToS asks for a real User-Agent and no more than 1 req/sec.
The cache (`geocode_cache`) means each unique (city, state, country)
combo only hits the service once for the life of the install.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from backend.integrations.geocoder.models import GeocodeCache
from backend.version import VERSION

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"SkyNetControl/{VERSION} (+https://github.com/ben-kuhn/SkyNetControl)"

_NEGATIVE_TTL_SECONDS = 7 * 24 * 3600  # re-try misses after a week

# Module-level lock + last-call timestamp implementing a polite 1 req/s
# cap. Multiple workers in one process serialize through this; multi-
# process deployments aren't a concern at this scale.
_rate_lock = threading.Lock()
_last_call_at: float = 0.0


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def _rate_limit() -> None:
    global _last_call_at
    with _rate_lock:
        elapsed = time.monotonic() - _last_call_at
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _last_call_at = time.monotonic()


def _call_nominatim(city: str, state: str, country: str) -> tuple[float, float] | None:
    """One real network call. Returns None on failure or no match."""
    _rate_limit()
    params = {
        "city": city,
        "state": state,
        "country": country,
        "format": "json",
        "limit": "1",
    }
    try:
        resp = httpx.get(
            NOMINATIM_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        logger.warning("Nominatim request failed for %s, %s: %s", city, state, exc)
        return None
    if resp.status_code != 200:
        logger.warning(
            "Nominatim returned %d for %s, %s", resp.status_code, city, state,
        )
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if not payload:
        return None
    first = payload[0]
    try:
        return (float(first["lat"]), float(first["lon"]))
    except (KeyError, TypeError, ValueError):
        return None


def geocode_city(
    db: Session,
    city: str | None,
    state: str | None,
    country: str = "United States",
) -> tuple[float, float] | None:
    """Return cached lat/lon for (city, state, country), querying Nominatim
    on first miss. Negative results are cached for a week so a misspelled
    city doesn't re-hit the service every check-in.
    """
    city_n = _normalize(city)
    state_n = _normalize(state)
    country_n = _normalize(country)
    if not city_n or not state_n:
        return None

    row = (
        db.query(GeocodeCache)
        .filter(
            GeocodeCache.city_norm == city_n,
            GeocodeCache.state_norm == state_n,
            GeocodeCache.country_norm == country_n,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if row is not None:
        if row.latitude is not None and row.longitude is not None:
            return (row.latitude, row.longitude)
        # Negative cache: only re-try after TTL elapses.
        fetched_at = row.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (now - fetched_at).total_seconds() < _NEGATIVE_TTL_SECONDS:
            return None
        # Stale negative — re-query, then update the row in place.

    coords = _call_nominatim(city.strip() if city else "", state.strip() if state else "", country)
    lat, lon = (coords if coords is not None else (None, None))

    if row is None:
        db.add(GeocodeCache(
            city_norm=city_n,
            state_norm=state_n,
            country_norm=country_n,
            city=(city or "").strip(),
            state=(state or "").strip(),
            country=country.strip(),
            latitude=lat,
            longitude=lon,
            fetched_at=now,
        ))
    else:
        row.latitude = lat
        row.longitude = lon
        row.fetched_at = now
    db.commit()
    return coords
