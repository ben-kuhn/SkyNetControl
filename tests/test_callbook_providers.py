from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base


def test_callbook_cache_model():
    """CallbookCache model can be created and queried."""
    from backend.integrations.callbook.models import CallbookCache

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as db:
        entry = CallbookCache(
            callsign="W0ABC",
            name="John Smith",
            city="Denver",
            county="Denver",
            state="CO",
            country="United States",
            latitude=39.7392,
            longitude=-104.9903,
            source="hamqth",
            fetched_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        db.add(entry)
        db.commit()

        result = db.get(CallbookCache, "W0ABC")
        assert result is not None
        assert result.name == "John Smith"
        assert result.source == "hamqth"
        assert result.latitude == 39.7392


from unittest.mock import patch, MagicMock


def test_callbook_result_dataclass():
    from backend.integrations.callbook.providers import CallbookResult

    result = CallbookResult(
        callsign="W0ABC",
        name="John Smith",
        city="Denver",
        county="Denver",
        state="CO",
        country="United States",
        latitude=39.7392,
        longitude=-104.9903,
        source="hamqth",
    )
    assert result.callsign == "W0ABC"
    assert result.source == "hamqth"


def test_hamqth_authenticate():
    from backend.integrations.callbook.providers import HamQTHProvider

    auth_xml = """<?xml version="1.0"?>
    <HamQTH version="2.7">
      <session>
        <session_id>abc123</session_id>
      </session>
    </HamQTH>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = auth_xml

    with patch("backend.integrations.callbook.providers.httpx.get", return_value=mock_resp):
        provider = HamQTHProvider()
        token = provider.authenticate("user", "pass")

    assert token == "abc123"


def test_hamqth_lookup_success():
    from backend.integrations.callbook.providers import HamQTHProvider

    lookup_xml = """<?xml version="1.0"?>
    <HamQTH version="2.7">
      <search>
        <callsign>W0ABC</callsign>
        <adr_name>John Smith</adr_name>
        <adr_city>Denver</adr_city>
        <us_county>Denver</us_county>
        <us_state>CO</us_state>
        <adr_country>United States</adr_country>
        <latitude>39.7392</latitude>
        <longitude>-104.9903</longitude>
      </search>
    </HamQTH>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = lookup_xml

    with patch("backend.integrations.callbook.providers.httpx.get", return_value=mock_resp):
        provider = HamQTHProvider()
        result = provider.lookup("W0ABC", "abc123")

    assert result is not None
    assert result.callsign == "W0ABC"
    assert result.name == "John Smith"
    assert result.city == "Denver"
    assert result.county == "Denver"
    assert result.state == "CO"
    assert result.latitude == 39.7392
    assert result.source == "hamqth"


def test_hamqth_lookup_not_found():
    from backend.integrations.callbook.providers import HamQTHProvider

    not_found_xml = """<?xml version="1.0"?>
    <HamQTH version="2.7">
      <session>
        <error>Callsign not found</error>
      </session>
    </HamQTH>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = not_found_xml

    with patch("backend.integrations.callbook.providers.httpx.get", return_value=mock_resp):
        provider = HamQTHProvider()
        result = provider.lookup("XXXXXX", "abc123")

    assert result is None


def test_hamqth_lookup_session_expired():
    """When HamQTH returns a session error, lookup returns None (caller retries auth)."""
    from backend.integrations.callbook.providers import HamQTHProvider

    expired_xml = """<?xml version="1.0"?>
    <HamQTH version="2.7">
      <session>
        <error>Session does not exist or has expired</error>
      </session>
    </HamQTH>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = expired_xml

    with patch("backend.integrations.callbook.providers.httpx.get", return_value=mock_resp):
        provider = HamQTHProvider()
        result = provider.lookup("W0ABC", "expired-token")

    assert result is None


def test_qrz_authenticate():
    from backend.integrations.callbook.providers import QRZProvider

    auth_xml = """<?xml version="1.0"?>
    <QRZDatabase version="1.34">
      <Session>
        <Key>xyz789</Key>
      </Session>
    </QRZDatabase>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = auth_xml

    with patch("backend.integrations.callbook.providers.httpx.get", return_value=mock_resp):
        provider = QRZProvider()
        token = provider.authenticate("user", "pass")

    assert token == "xyz789"


def test_qrz_lookup_success():
    from backend.integrations.callbook.providers import QRZProvider

    lookup_xml = """<?xml version="1.0"?>
    <QRZDatabase version="1.34">
      <Callsign>
        <call>W0ABC</call>
        <fname>John</fname>
        <name>Smith</name>
        <addr2>Denver</addr2>
        <county>Denver</county>
        <state>CO</state>
        <country>United States</country>
        <lat>39.7392</lat>
        <lon>-104.9903</lon>
      </Callsign>
    </QRZDatabase>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = lookup_xml

    with patch("backend.integrations.callbook.providers.httpx.get", return_value=mock_resp):
        provider = QRZProvider()
        result = provider.lookup("W0ABC", "xyz789")

    assert result is not None
    assert result.callsign == "W0ABC"
    assert result.name == "John Smith"
    assert result.city == "Denver"
    assert result.state == "CO"
    assert result.latitude == 39.7392
    assert result.source == "qrz"


def test_qrz_lookup_not_found():
    from backend.integrations.callbook.providers import QRZProvider

    not_found_xml = """<?xml version="1.0"?>
    <QRZDatabase version="1.34">
      <Session>
        <Error>Not found: XXXXXX</Error>
      </Session>
    </QRZDatabase>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = not_found_xml

    with patch("backend.integrations.callbook.providers.httpx.get", return_value=mock_resp):
        provider = QRZProvider()
        result = provider.lookup("XXXXXX", "xyz789")

    assert result is None
