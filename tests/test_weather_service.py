# tests/test_weather_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import Event, EventPost, EventType, EventStatus
from backend.modules.events.event_config_service import set_event_config
from backend.integrations.weather import service as weather_service
from backend.integrations.weather.client import WeatherUnavailable

EMPTY = {"type": "FeatureCollection", "features": []}
ONE = {"type": "FeatureCollection", "features": [{"id": "A", "properties": {"event": "Tornado Warning"}}]}


class FakeClient:
    def __init__(self, *, alerts=None, state="MN", unavailable=False):
        self._alerts = alerts if alerts is not None else ONE
        self._state = state
        self._unavailable = unavailable
        self.calls = 0

    def fetch_active_alerts(self, states):
        self.calls += 1
        if self._unavailable:
            raise WeatherUnavailable("down")
        return self._alerts

    def lookup_state(self, lat, lon):
        return self._state


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by="W0NE", public_token="test_token", aprs_range_lat=44.98, aprs_range_lon=-93.27)
    db.add(ev); db.flush()
    return db, ev.id


def setup_function():
    weather_service.clear_weather_cache()


def test_disabled_returns_disabled_status():
    db, ev_id = _db()
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient())
    assert r["status"] == "disabled"
    assert r["alerts"] == EMPTY


def test_enabled_with_configured_states_fetches_ok():
    db, ev_id = _db()
    set_event_config(db, ev_id, "weather.enabled", "true")
    set_event_config(db, ev_id, "weather.alert_states", '["MN"]')
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient(alerts=ONE))
    assert r["status"] == "ok"
    assert r["alerts"]["features"][0]["id"] == "A"
    assert r["updated_at"] is not None


def test_area_derived_from_event_location_when_no_states():
    db, ev_id = _db()
    set_event_config(db, ev_id, "weather.enabled", "true")
    fc = FakeClient(state="MN")
    r = weather_service.get_event_alerts(db, ev_id, client=fc)
    assert r["status"] == "ok"  # derived MN from aprs_range_lat/lon


def test_no_area_when_no_states_and_no_location():
    db, ev_id = _db()
    set_event_config(db, ev_id, "weather.enabled", "true")
    # remove the event location + posts
    ev = db.get(Event, ev_id); ev.aprs_range_lat = None; ev.aprs_range_lon = None; db.commit()
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient(state=None))
    assert r["status"] == "no_area"


def test_cache_shared_within_ttl():
    db, ev_id = _db()
    set_event_config(db, ev_id, "weather.enabled", "true")
    set_event_config(db, ev_id, "weather.alert_states", '["MN"]')
    fc = FakeClient()
    weather_service.get_event_alerts(db, ev_id, client=fc, now=1000.0)
    weather_service.get_event_alerts(db, ev_id, client=fc, now=1030.0)  # within 60s
    assert fc.calls == 1  # second served from cache
    weather_service.get_event_alerts(db, ev_id, client=fc, now=1100.0)  # past TTL
    assert fc.calls == 2


def test_stale_while_error_serves_last_good():
    db, ev_id = _db()
    set_event_config(db, ev_id, "weather.enabled", "true")
    set_event_config(db, ev_id, "weather.alert_states", '["MN"]')
    weather_service.get_event_alerts(db, ev_id, client=FakeClient(alerts=ONE), now=1000.0)
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient(unavailable=True), now=1100.0)
    assert r["status"] == "stale"
    assert r["alerts"]["features"][0]["id"] == "A"  # last good retained


def test_unavailable_with_no_cache():
    db, ev_id = _db()
    set_event_config(db, ev_id, "weather.enabled", "true")
    set_event_config(db, ev_id, "weather.alert_states", '["MN"]')
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient(unavailable=True))
    assert r["status"] == "unavailable"
    assert r["alerts"] == EMPTY
