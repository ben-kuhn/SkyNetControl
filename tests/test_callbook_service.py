import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.config_mgmt.models import AppConfig
from backend.integrations.callbook.models import CallbookCache
from backend.integrations.callbook.providers import CallbookResult


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        yield session


def _set_config(db, key, value):
    db.add(AppConfig(key=key, value=value))
    db.commit()


def test_lookup_returns_fresh_cache(db):
    from backend.integrations.callbook.service import lookup_callsign

    db.add(
        CallbookCache(
            callsign="W0ABC",
            name="John Smith",
            city="Denver",
            county="Denver",
            state="CO",
            country="United States",
            latitude=39.7392,
            longitude=-104.9903,
            source="hamqth",
            fetched_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    result = lookup_callsign(db, "W0ABC")
    assert result is not None
    assert result["callsign"] == "W0ABC"
    assert result["name"] == "John Smith"
    assert result["cached"] is True


def test_lookup_skips_expired_cache(db):
    from backend.integrations.callbook.service import lookup_callsign

    db.add(
        CallbookCache(
            callsign="W0OLD",
            name="Old Entry",
            city="Denver",
            state="CO",
            source="hamqth",
            fetched_at=datetime.now(timezone.utc) - timedelta(days=31),
        )
    )
    db.commit()

    _set_config(db, "callbook.providers", json.dumps(["hamqth"]))
    _set_config(db, "callbook.hamqth.username", "user")
    _set_config(db, "callbook.hamqth.password", "pass")

    mock_result = CallbookResult(
        callsign="W0OLD",
        name="Updated Name",
        city="Boulder",
        county="Boulder",
        state="CO",
        country="United States",
        latitude=40.0,
        longitude=-105.0,
        source="hamqth",
    )

    with patch("backend.integrations.callbook.service._lookup_from_provider", return_value=mock_result):
        result = lookup_callsign(db, "W0OLD")

    assert result is not None
    assert result["name"] == "Updated Name"
    assert result["cached"] is False

    cached = db.get(CallbookCache, "W0OLD")
    assert cached.name == "Updated Name"


def test_lookup_tries_providers_in_order(db):
    from backend.integrations.callbook.service import lookup_callsign

    _set_config(db, "callbook.providers", json.dumps(["hamqth", "qrz"]))
    _set_config(db, "callbook.hamqth.username", "user")
    _set_config(db, "callbook.hamqth.password", "pass")
    _set_config(db, "callbook.qrz.username", "user2")
    _set_config(db, "callbook.qrz.password", "pass2")

    qrz_result = CallbookResult(
        callsign="W0NEW",
        name="From QRZ",
        city="Denver",
        county=None,
        state="CO",
        country="United States",
        latitude=None,
        longitude=None,
        source="qrz",
    )

    with patch("backend.integrations.callbook.service._lookup_from_provider", side_effect=[None, qrz_result]):
        result = lookup_callsign(db, "W0NEW")

    assert result is not None
    assert result["name"] == "From QRZ"
    assert result["source"] == "qrz"


def test_lookup_returns_none_when_no_providers_configured(db):
    from backend.integrations.callbook.service import lookup_callsign

    result = lookup_callsign(db, "W0ABC")
    assert result is None


def test_is_callbook_configured_false_when_empty(db):
    from backend.integrations.callbook.service import is_callbook_configured

    assert is_callbook_configured(db) is False


def test_is_callbook_configured_false_when_credentials_missing(db):
    from backend.integrations.callbook.service import is_callbook_configured

    _set_config(db, "callbook.providers", json.dumps(["hamqth"]))
    # username and password not set
    assert is_callbook_configured(db) is False


def test_is_callbook_configured_true_when_provider_has_credentials(db):
    from backend.integrations.callbook.service import is_callbook_configured

    _set_config(db, "callbook.providers", json.dumps(["hamqth"]))
    _set_config(db, "callbook.hamqth.username", "user")
    _set_config(db, "callbook.hamqth.password", "pass")
    assert is_callbook_configured(db) is True


def test_lookup_returns_none_when_all_providers_fail(db):
    from backend.integrations.callbook.service import lookup_callsign

    _set_config(db, "callbook.providers", json.dumps(["hamqth"]))
    _set_config(db, "callbook.hamqth.username", "user")
    _set_config(db, "callbook.hamqth.password", "pass")

    with patch("backend.integrations.callbook.service._lookup_from_provider", return_value=None):
        result = lookup_callsign(db, "XXXXXX")

    assert result is None


def test_lookup_from_provider_retries_on_auth_failure(db):
    from backend.integrations.callbook.service import _lookup_from_provider, _session_tokens
    from backend.integrations.callbook.providers import HamQTHProvider

    _session_tokens["hamqth"] = "expired-token"

    fresh_result = CallbookResult(
        callsign="W0ABC",
        name="John",
        city="Denver",
        county=None,
        state="CO",
        country="United States",
        latitude=None,
        longitude=None,
        source="hamqth",
    )

    provider = HamQTHProvider()
    with (
        patch.object(provider, "lookup", side_effect=[None, fresh_result]) as mock_lookup,
        patch.object(provider, "authenticate", return_value="new-token") as mock_auth,
    ):
        result = _lookup_from_provider(provider, "W0ABC", "user", "pass", "hamqth")

    assert result is not None
    assert result.name == "John"
    mock_auth.assert_called_once_with("user", "pass")
    assert _session_tokens["hamqth"] == "new-token"

    _session_tokens.pop("hamqth", None)
