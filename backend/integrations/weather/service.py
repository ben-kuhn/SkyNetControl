# backend/integrations/weather/service.py
"""Event weather alerts: resolve the event's NWS area, fetch active alerts with a
short shared TTL cache (cache-on-read, no background task), and degrade gracefully
when NWS is unavailable."""
from __future__ import annotations

import json
import re
import time as _time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.modules.events.models import Event, EventPost
from backend.modules.events.event_config_service import get_event_config, event_from_callsign
from backend.integrations.weather.client import WeatherClient, WeatherUnavailable

WEATHER_TTL_SECONDS = 60.0
_EMPTY = {"type": "FeatureCollection", "features": []}

# area-key (tuple of sorted states) -> (alerts FeatureCollection, updated_at ISO, fetched_monotonic)
_CACHE: dict[tuple[str, ...], tuple[dict, str, float]] = {}


def clear_weather_cache() -> None:
    _CACHE.clear()


def _weather_enabled(db: Session, event: Event) -> bool:
    return (get_event_config(db, event.id, "weather.enabled") or "").strip().lower() == "true"


def _user_agent(db: Session, event: Event) -> str:
    contact = (get_event_config(db, event.id, "weather.nws_contact") or "").strip()
    if not contact:
        na = (get_event_config(db, event.id, "net_address") or "").strip()
        contact = na or event_from_callsign(db, event)
    return f"SkyNetControl ({contact})" if contact else "SkyNetControl"


def _event_location(db: Session, event: Event) -> tuple[float, float] | None:
    if event.aprs_range_lat is not None and event.aprs_range_lon is not None:
        return (event.aprs_range_lat, event.aprs_range_lon)
    coords = [(p.lat, p.lon) for p in db.query(EventPost).filter(EventPost.event_id == event.id).all()
              if p.lat is not None and p.lon is not None]
    if not coords:
        return None
    return (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))


def _parse_state_codes(raw: str | None) -> list[str]:
    """Accept a JSON list (``["MN","WI"]``) OR a comma/space-separated string
    (``MN, WI``) of 2-letter state codes. Empty/unparseable -> ``[]``."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(s).strip().upper() for s in parsed if str(s).strip()]
    except (json.JSONDecodeError, ValueError):
        pass
    return [tok.strip().upper() for tok in re.split(r"[,\s]+", raw) if tok.strip()]


def _resolve_states(db: Session, event: Event, client: WeatherClient) -> list[str]:
    states = _parse_state_codes(get_event_config(db, event.id, "weather.alert_states"))
    if states:
        return states
    loc = _event_location(db, event)
    if loc is None:
        return []
    state = client.lookup_state(loc[0], loc[1])
    return [state] if state else []


def get_event_alerts(db: Session, event_id: int, *, client: WeatherClient | None = None,
                     now: float | None = None) -> dict:
    event = db.get(Event, event_id)
    if event is None:
        return {"alerts": _EMPTY, "updated_at": None, "status": "no_area"}
    if not _weather_enabled(db, event):
        return {"alerts": _EMPTY, "updated_at": None, "status": "disabled"}

    if client is None:
        client = WeatherClient(user_agent=_user_agent(db, event))
    if now is None:
        now = _time.monotonic()

    try:
        states = _resolve_states(db, event, client)
    except WeatherUnavailable:
        states = []
    if not states:
        return {"alerts": _EMPTY, "updated_at": None, "status": "no_area"}

    key = tuple(sorted(states))
    cached = _CACHE.get(key)
    if cached is not None and (now - cached[2]) < WEATHER_TTL_SECONDS:
        return {"alerts": cached[0], "updated_at": cached[1], "status": "ok"}

    try:
        alerts = client.fetch_active_alerts(list(key))
    except WeatherUnavailable:
        if cached is not None:
            return {"alerts": cached[0], "updated_at": cached[1], "status": "stale"}
        return {"alerts": _EMPTY, "updated_at": None, "status": "unavailable"}

    updated_at = datetime.now(timezone.utc).isoformat()
    _CACHE[key] = (alerts, updated_at, now)
    return {"alerts": alerts, "updated_at": updated_at, "status": "ok"}
