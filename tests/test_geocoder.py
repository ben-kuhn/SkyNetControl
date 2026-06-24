"""Nominatim geocoder cache. The real HTTP call is mocked — we test the
cache logic, not the network."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.integrations.geocoder import service as geocoder_service
from backend.integrations.geocoder.models import GeocodeCache


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_geocode_caches_on_first_hit(db, monkeypatch):
    """First call hits the network; second call returns from cache without
    a network call."""
    calls = []

    def fake_call(city, state, country):
        calls.append((city, state, country))
        return (39.7392, -104.9903)

    monkeypatch.setattr(geocoder_service, "_call_nominatim", fake_call)

    coords = geocoder_service.geocode_city(db, "Denver", "CO")
    assert coords == (39.7392, -104.9903)
    assert len(calls) == 1

    # Second call — same city, same state. No new network call.
    coords2 = geocoder_service.geocode_city(db, "Denver", "CO")
    assert coords2 == (39.7392, -104.9903)
    assert len(calls) == 1


def test_geocode_returns_none_when_no_city_or_state(db, monkeypatch):
    """Missing inputs short-circuit before any network call."""
    monkeypatch.setattr(
        geocoder_service, "_call_nominatim",
        lambda *a, **kw: pytest.fail("should not call"),
    )
    assert geocoder_service.geocode_city(db, None, "CO") is None
    assert geocoder_service.geocode_city(db, "Denver", None) is None
    assert geocoder_service.geocode_city(db, "", "CO") is None


def test_negative_cache_prevents_immediate_requery(db, monkeypatch):
    """A miss is cached too — a misspelled city name shouldn't re-hit
    Nominatim on every check-in."""
    calls = []

    def fake_call(city, state, country):
        calls.append((city, state, country))
        return None

    monkeypatch.setattr(geocoder_service, "_call_nominatim", fake_call)

    assert geocoder_service.geocode_city(db, "Notarealcity", "ZZ") is None
    assert geocoder_service.geocode_city(db, "Notarealcity", "ZZ") is None
    assert len(calls) == 1


def test_stale_negative_cache_retries(db, monkeypatch):
    """After the negative-cache TTL elapses, we re-try in case the upstream
    catalog has been updated."""
    monkeypatch.setattr(geocoder_service, "_call_nominatim", lambda *a, **kw: None)
    geocoder_service.geocode_city(db, "Newville", "VT")

    # Backdate the cache entry past the TTL.
    row = db.query(GeocodeCache).filter(GeocodeCache.city_norm == "newville").one()
    row.fetched_at = datetime.now(timezone.utc) - timedelta(days=14)
    db.commit()

    calls = []

    def fake_hit(city, state, country):
        calls.append(1)
        return (44.4, -72.0)

    monkeypatch.setattr(geocoder_service, "_call_nominatim", fake_hit)
    coords = geocoder_service.geocode_city(db, "Newville", "VT")
    assert coords == (44.4, -72.0)
    assert len(calls) == 1


def test_geocode_lookup_is_case_insensitive(db, monkeypatch):
    """`Denver, CO` and `denver, co` are the same cache key."""
    calls = []
    monkeypatch.setattr(
        geocoder_service, "_call_nominatim",
        lambda city, state, country: calls.append(1) or (1.0, 2.0),
    )

    geocoder_service.geocode_city(db, "Denver", "CO")
    geocoder_service.geocode_city(db, "denver", "co")
    assert len(calls) == 1
