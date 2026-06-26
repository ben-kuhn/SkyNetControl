"""City/state <-> lat/lon geocoding, with DB-backed caches.

Forward (city/state -> lat/lon) uses OpenStreetMap Nominatim.
Reverse (lat/lon -> closest populated place) uses Overpass, because
Nominatim's reverse endpoint returns the admin area containing the
point — useless for rural ham operators outside any city limits.

Both services ask for a real User-Agent and reasonable request rates.
The caches mean each unique key only hits the network once.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from backend.integrations.geocoder.models import GeocodeCache, ReverseGeocodeCache
from backend.version import VERSION

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = f"SkyNetControl/{VERSION} (+https://github.com/ben-kuhn/SkyNetControl)"

_NEGATIVE_TTL_SECONDS = 7 * 24 * 3600  # re-try misses after a week

# Search radius for the closest populated place. 50 km comfortably covers
# rural US — most operators are within that of *some* town.
_OVERPASS_SEARCH_RADIUS_M = 50_000

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


def _bucket_coord(value: float) -> int:
    """Round a lat/lon to two decimals and store as integer hundredths.
    The integer key avoids floating-point equality issues in the unique
    constraint while still bucketing ~1 km of jitter into one cache row.
    """
    return int(round(value * 100))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _call_overpass_closest_place(lat: float, lon: float) -> tuple[str, str | None] | None:
    """Overpass query: closest `place=city|town|village` node to (lat, lon),
    plus the containing state (admin_level=4) if present. Returns
    `(city_name, state_name_or_None)` or None on failure / no result.
    """
    _rate_limit()
    # Overpass QL:
    #   - Pull candidate populated-place nodes within the search radius.
    #   - Pull the admin boundary at the point (state).
    #   - Emit tags only; lat/lon are part of each node's metadata.
    query = (
        f"[out:json][timeout:25];"
        f'(node["place"~"^(city|town|village)$"]'
        f"(around:{_OVERPASS_SEARCH_RADIUS_M},{lat},{lon}););"
        f"out body;"
        f"is_in({lat},{lon})->.a;"
        f'relation.a["admin_level"="4"]["boundary"="administrative"];'
        f"out tags;"
    )
    try:
        resp = httpx.post(
            OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        logger.warning("Overpass request failed for %s,%s: %s", lat, lon, exc)
        return None
    if resp.status_code != 200:
        logger.warning("Overpass returned %d for %s,%s", resp.status_code, lat, lon)
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None

    elements = payload.get("elements") or []
    closest_name: str | None = None
    closest_km = float("inf")
    state_name: str | None = None
    for el in elements:
        tags = el.get("tags") or {}
        if el.get("type") == "node":
            name = tags.get("name")
            el_lat = el.get("lat")
            el_lon = el.get("lon")
            if not name or el_lat is None or el_lon is None:
                continue
            d = _haversine_km(lat, lon, float(el_lat), float(el_lon))
            if d < closest_km:
                closest_km = d
                closest_name = name
        elif el.get("type") == "relation":
            # The admin boundary; pick the name regardless of which relation
            # came back if multiple match (we asked for level 4).
            if state_name is None:
                state_name = tags.get("name")

    if closest_name is None:
        return None
    return (closest_name, state_name)


def reverse_geocode_closest_city(
    db: Session,
    latitude: float | None,
    longitude: float | None,
) -> tuple[str, str | None] | None:
    """Return `(city, state)` for the closest populated place to the given
    coordinates. Returns None if coords are missing/zero, the lookup fails,
    or no populated place exists within the search radius.

    Results are cached per ~1 km bucket. Negative results are cached too
    so points in the middle of the ocean don't keep hitting Overpass.
    """
    if latitude is None or longitude is None:
        return None
    # Treat exact 0,0 as missing — common form-default sentinel, and
    # there's no useful "closest city" to Null Island anyway.
    if latitude == 0.0 and longitude == 0.0:
        return None

    lat_key = _bucket_coord(latitude)
    lon_key = _bucket_coord(longitude)

    row = (
        db.query(ReverseGeocodeCache)
        .filter(
            ReverseGeocodeCache.lat_key == lat_key,
            ReverseGeocodeCache.lon_key == lon_key,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if row is not None:
        if row.city is not None:
            return (row.city, row.state)
        fetched_at = row.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (now - fetched_at).total_seconds() < _NEGATIVE_TTL_SECONDS:
            return None
        # Stale negative — re-query, then update the row in place.

    result = _call_overpass_closest_place(latitude, longitude)
    city, state = (result if result is not None else (None, None))

    if row is None:
        db.add(ReverseGeocodeCache(
            lat_key=lat_key,
            lon_key=lon_key,
            city=city,
            state=state,
            fetched_at=now,
        ))
    else:
        row.city = city
        row.state = state
        row.fetched_at = now
    db.commit()
    return result
