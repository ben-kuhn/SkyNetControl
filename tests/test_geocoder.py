"""Nominatim geocoder cache. The real HTTP call is mocked — we test the
cache logic, not the network."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.integrations.geocoder import service as geocoder_service
from backend.integrations.geocoder.models import GeocodeCache, ReverseGeocodeCache


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


def test_reverse_geocode_caches_on_first_hit(db, monkeypatch):
    """First call hits Overpass; second call returns from cache."""
    calls = []

    def fake_overpass(lat, lon):
        calls.append((lat, lon))
        return ("Marquette", "Michigan")

    monkeypatch.setattr(geocoder_service, "_call_overpass_closest_place", fake_overpass)

    result = geocoder_service.reverse_geocode_closest_city(db, 46.5625, -87.375)
    assert result == ("Marquette", "Michigan")
    assert len(calls) == 1

    # Same coords → cache hit.
    result2 = geocoder_service.reverse_geocode_closest_city(db, 46.5625, -87.375)
    assert result2 == ("Marquette", "Michigan")
    assert len(calls) == 1


def test_reverse_geocode_buckets_nearby_coords_into_one_row(db, monkeypatch):
    """Lat/lon within the ~1 km bucket share a cache entry."""
    calls = []
    monkeypatch.setattr(
        geocoder_service, "_call_overpass_closest_place",
        lambda lat, lon: calls.append(1) or ("Marquette", "Michigan"),
    )

    geocoder_service.reverse_geocode_closest_city(db, 46.5625, -87.3750)
    # Slight GPS jitter — within the same 2-decimal bucket, no extra call.
    geocoder_service.reverse_geocode_closest_city(db, 46.5630, -87.3753)
    assert len(calls) == 1


def test_reverse_geocode_returns_none_for_missing_or_zero_coords(db, monkeypatch):
    """Null Island and missing coords short-circuit before the network."""
    monkeypatch.setattr(
        geocoder_service, "_call_overpass_closest_place",
        lambda *a, **kw: pytest.fail("should not call"),
    )
    assert geocoder_service.reverse_geocode_closest_city(db, None, -87.0) is None
    assert geocoder_service.reverse_geocode_closest_city(db, 46.0, None) is None
    assert geocoder_service.reverse_geocode_closest_city(db, 0.0, 0.0) is None


def test_reverse_geocode_negative_cache_prevents_immediate_requery(db, monkeypatch):
    """A coordinate with no nearby populated place is cached too."""
    calls = []

    def fake(lat, lon):
        calls.append(1)
        return None

    monkeypatch.setattr(geocoder_service, "_call_overpass_closest_place", fake)
    assert geocoder_service.reverse_geocode_closest_city(db, 20.0, -150.0) is None
    assert geocoder_service.reverse_geocode_closest_city(db, 20.0, -150.0) is None
    assert len(calls) == 1


def test_reverse_geocode_stale_negative_retries(db, monkeypatch):
    """After the negative-cache TTL elapses, we re-try."""
    monkeypatch.setattr(geocoder_service, "_call_overpass_closest_place", lambda lat, lon: None)
    geocoder_service.reverse_geocode_closest_city(db, 41.0, -90.0)

    row = db.query(ReverseGeocodeCache).one()
    row.fetched_at = datetime.now(timezone.utc) - timedelta(days=14)
    db.commit()

    calls = []

    def fake_hit(lat, lon):
        calls.append(1)
        return ("Quad Cities", "Iowa")

    monkeypatch.setattr(geocoder_service, "_call_overpass_closest_place", fake_hit)
    assert geocoder_service.reverse_geocode_closest_city(db, 41.0, -90.0) == ("Quad Cities", "Iowa")
    assert len(calls) == 1
