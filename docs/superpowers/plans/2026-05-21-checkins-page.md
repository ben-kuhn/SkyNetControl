# Check-ins Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Check-ins frontend page with session-scoped check-in management, and add callbook lookup integration (HamQTH + QRZ) with caching for auto-filling operator details.

**Architecture:** A new callbook integration backend (providers + cache + service) exposes a lookup endpoint on the existing checkins router. The React frontend replaces the placeholder Check-ins page with a session-scoped view showing check-ins, action buttons, and modals for add/edit with callbook lookup.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, httpx, React/TypeScript

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `backend/integrations/callbook/__init__.py` | Package marker |
| `backend/integrations/callbook/models.py` | `CallbookCache` SQLAlchemy model |
| `backend/integrations/callbook/providers.py` | `CallbookResult` dataclass, `HamQTHProvider`, `QRZProvider` classes |
| `backend/integrations/callbook/service.py` | `lookup_callsign()` orchestration with caching |
| `frontend/src/pages/CheckInsPage.tsx` | Full check-ins page component |
| `frontend/src/api/checkins.ts` | API client functions for check-ins + callbook lookup |
| `tests/test_callbook_providers.py` | Provider unit tests with mocked HTTP |
| `tests/test_callbook_service.py` | Service tests (cache, fallback, expiry) |
| `tests/test_callbook_routes.py` | Lookup endpoint route tests |

**Modified files:**

| File | Change |
|------|--------|
| `backend/modules/checkins/routes.py` | Add `GET /lookup/{callsign}` endpoint |
| `frontend/src/types/index.ts` | Add `CheckIn` and `CallbookResult` interfaces |
| `frontend/src/App.tsx` | Replace placeholder with `<CheckInsPage />` |
| `frontend/src/pages/SchedulePage.tsx` | Add "View check-ins" link per session row |
| `alembic/env.py` | Import `backend.integrations.callbook.models` |

---

### Task 1: CallbookCache Model and Migration

**Files:**
- Create: `backend/integrations/callbook/__init__.py`
- Create: `backend/integrations/callbook/models.py`
- Modify: `alembic/env.py`
- Test: `tests/test_callbook_providers.py` (model instantiation test)

- [ ] **Step 1: Write the failing test**

Create `tests/test_callbook_providers.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "python -m pytest tests/test_callbook_providers.py::test_callbook_cache_model -v" shell.nix`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.integrations.callbook'`

- [ ] **Step 3: Create the model and package files**

Create `backend/integrations/callbook/__init__.py` (empty file).

Create `backend/integrations/callbook/models.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class CallbookCache(Base):
    __tablename__ = "callbook_cache"

    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    county: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Add import to alembic/env.py**

Add this line after the existing delivery import in `alembic/env.py`:

```python
import backend.integrations.callbook.models  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `nix-shell --run "python -m pytest tests/test_callbook_providers.py::test_callbook_cache_model -v" shell.nix`
Expected: PASS

- [ ] **Step 6: Generate Alembic migration**

Run: `nix-shell --run "alembic revision --autogenerate -m 'add callbook_cache table'" shell.nix`
Verify the generated migration creates the `callbook_cache` table.

- [ ] **Step 7: Commit**

```bash
git add backend/integrations/callbook/__init__.py backend/integrations/callbook/models.py alembic/env.py alembic/versions/*.py tests/test_callbook_providers.py
git commit -m "feat: add CallbookCache model and migration"
```

---

### Task 2: Callbook Providers (HamQTH + QRZ)

**Files:**
- Create: `backend/integrations/callbook/providers.py`
- Test: `tests/test_callbook_providers.py` (append)

- [ ] **Step 1: Write the failing tests for CallbookResult and HamQTHProvider**

Append to `tests/test_callbook_providers.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_callbook_providers.py -v -k 'not cache_model'" shell.nix`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.integrations.callbook.providers'`

- [ ] **Step 3: Implement providers**

Create `backend/integrations/callbook/providers.py`:

```python
from dataclasses import dataclass
from xml.etree import ElementTree

import httpx


@dataclass
class CallbookResult:
    callsign: str
    name: str | None
    city: str | None
    county: str | None
    state: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    source: str


def _parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


class HamQTHProvider:
    """HamQTH.com XML callbook provider."""

    BASE_URL = "https://www.hamqth.com/xml.php"

    def authenticate(self, username: str, password: str) -> str:
        resp = httpx.get(
            self.BASE_URL,
            params={"u": username, "p": password},
            timeout=15,
        )
        root = ElementTree.fromstring(resp.text)
        ns = {"h": "https://www.hamqth.com"}
        session_id = root.find(".//h:session_id", ns)
        if session_id is None:
            # Try without namespace (some responses omit it)
            session_id = root.find(".//session_id")
        if session_id is None or not session_id.text:
            raise ValueError("HamQTH authentication failed")
        return session_id.text

    def lookup(self, callsign: str, session_token: str) -> CallbookResult | None:
        resp = httpx.get(
            self.BASE_URL,
            params={"id": session_token, "callsign": callsign, "prg": "SkyNetControl"},
            timeout=15,
        )
        root = ElementTree.fromstring(resp.text)
        ns = {"h": "https://www.hamqth.com"}

        # Check for error (not found or session expired)
        error = root.find(".//h:error", ns)
        if error is None:
            error = root.find(".//error")
        if error is not None:
            return None

        search = root.find(".//h:search", ns)
        if search is None:
            search = root.find(".//search")
        if search is None:
            return None

        def _get(tag: str) -> str | None:
            el = search.find(f"h:{tag}", ns)
            if el is None:
                el = search.find(tag)
            return el.text if el is not None else None

        name = _get("nick") or _get("adr_name")

        return CallbookResult(
            callsign=callsign.upper(),
            name=name,
            city=_get("adr_city"),
            county=_get("us_county"),
            state=_get("us_state"),
            country=_get("adr_country"),
            latitude=_parse_float(_get("latitude")),
            longitude=_parse_float(_get("longitude")),
            source="hamqth",
        )


class QRZProvider:
    """QRZ.com XML callbook provider."""

    BASE_URL = "https://xmldata.qrz.com/xml/current/"

    def authenticate(self, username: str, password: str) -> str:
        resp = httpx.get(
            self.BASE_URL,
            params={"username": username, "password": password},
            timeout=15,
        )
        root = ElementTree.fromstring(resp.text)
        ns = {"q": "http://xmldata.qrz.com"}
        key = root.find(".//q:Key", ns)
        if key is None:
            key = root.find(".//Key")
        if key is None or not key.text:
            raise ValueError("QRZ authentication failed")
        return key.text

    def lookup(self, callsign: str, session_token: str) -> CallbookResult | None:
        resp = httpx.get(
            self.BASE_URL,
            params={"s": session_token, "callsign": callsign},
            timeout=15,
        )
        root = ElementTree.fromstring(resp.text)
        ns = {"q": "http://xmldata.qrz.com"}

        # Check for error
        error = root.find(".//q:Error", ns)
        if error is None:
            error = root.find(".//Error")
        if error is not None:
            return None

        record = root.find(".//q:Callsign", ns)
        if record is None:
            record = root.find(".//Callsign")
        if record is None:
            return None

        def _get(tag: str) -> str | None:
            el = record.find(f"q:{tag}", ns)
            if el is None:
                el = record.find(tag)
            return el.text if el is not None else None

        fname = _get("fname") or ""
        lname = _get("name") or ""
        full_name = f"{fname} {lname}".strip() or None

        return CallbookResult(
            callsign=callsign.upper(),
            name=full_name,
            city=_get("addr2"),
            county=_get("county"),
            state=_get("state"),
            country=_get("country"),
            latitude=_parse_float(_get("lat")),
            longitude=_parse_float(_get("lon")),
            source="qrz",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_callbook_providers.py -v" shell.nix`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/callbook/providers.py tests/test_callbook_providers.py
git commit -m "feat: add HamQTH and QRZ callbook providers"
```

---

### Task 3: Callbook Lookup Service (Cache + Orchestration)

**Files:**
- Create: `backend/integrations/callbook/service.py`
- Create: `tests/test_callbook_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_callbook_service.py`:

```python
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

    db.add(CallbookCache(
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
    ))
    db.commit()

    result = lookup_callsign(db, "W0ABC")
    assert result is not None
    assert result["callsign"] == "W0ABC"
    assert result["name"] == "John Smith"
    assert result["cached"] is True


def test_lookup_skips_expired_cache(db):
    from backend.integrations.callbook.service import lookup_callsign

    db.add(CallbookCache(
        callsign="W0OLD",
        name="Old Entry",
        city="Denver",
        state="CO",
        source="hamqth",
        fetched_at=datetime.now(timezone.utc) - timedelta(days=31),
    ))
    db.commit()

    _set_config(db, "callbook.providers", json.dumps(["hamqth"]))
    _set_config(db, "callbook.hamqth.username", "user")
    _set_config(db, "callbook.hamqth.password", "pass")

    mock_result = CallbookResult(
        callsign="W0OLD", name="Updated Name", city="Boulder",
        county="Boulder", state="CO", country="United States",
        latitude=40.0, longitude=-105.0, source="hamqth",
    )

    with patch("backend.integrations.callbook.service._lookup_from_provider", return_value=mock_result):
        result = lookup_callsign(db, "W0OLD")

    assert result is not None
    assert result["name"] == "Updated Name"
    assert result["cached"] is False

    # Verify cache was updated
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
        callsign="W0NEW", name="From QRZ", city="Denver",
        county=None, state="CO", country="United States",
        latitude=None, longitude=None, source="qrz",
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

    # Pre-seed an expired token
    _session_tokens["hamqth"] = "expired-token"

    fresh_result = CallbookResult(
        callsign="W0ABC", name="John", city="Denver",
        county=None, state="CO", country="United States",
        latitude=None, longitude=None, source="hamqth",
    )

    provider = HamQTHProvider()
    # First lookup returns None (expired), re-auth succeeds, second lookup works
    with patch.object(provider, "lookup", side_effect=[None, fresh_result]) as mock_lookup, \
         patch.object(provider, "authenticate", return_value="new-token") as mock_auth:
        result = _lookup_from_provider(provider, "W0ABC", "user", "pass", "hamqth")

    assert result is not None
    assert result.name == "John"
    mock_auth.assert_called_once_with("user", "pass")
    assert _session_tokens["hamqth"] == "new-token"

    # Clean up module state
    _session_tokens.pop("hamqth", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_callbook_service.py -v" shell.nix`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.integrations.callbook.service'`

- [ ] **Step 3: Implement the service**

Create `backend/integrations/callbook/service.py`:

```python
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

# In-memory session token cache: {"hamqth": "token", "qrz": "token"}
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
    """Try lookup with cached session token, re-auth once on failure."""
    token = _session_tokens.get(provider_name)

    if token:
        result = provider.lookup(callsign, token)
        if result is not None:
            return result

    # Auth (or re-auth) and retry
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
        db.add(CallbookCache(
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
        ))
    db.commit()


def lookup_callsign(db: Session, callsign: str) -> dict | None:
    """Look up a callsign. Returns dict with result + cached flag, or None."""
    callsign = callsign.upper()

    # Check cache
    cached = db.get(CallbookCache, callsign)
    if cached:
        age = datetime.now(timezone.utc) - cached.fetched_at.replace(tzinfo=timezone.utc)
        if age < timedelta(days=CACHE_TTL_DAYS):
            return _cache_to_dict(cached, cached=True)

    # Get provider list
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_callbook_service.py -v" shell.nix`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/callbook/service.py tests/test_callbook_service.py
git commit -m "feat: add callbook lookup service with caching"
```

---

### Task 4: Callbook Lookup Route

**Files:**
- Modify: `backend/modules/checkins/routes.py`
- Create: `tests/test_callbook_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_callbook_routes.py`:

```python
import json
from datetime import date
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config import Settings
from backend.config_mgmt.models import AppConfig
from backend.modules.checkins.routes import checkins_router


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def db_setup():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(callsign="W0NC", oidc_subject="auth0|nc", name="Net Control", role=UserRole.NET_CONTROL))
        session.add(User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer", role=UserRole.VIEWER))
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(checkins_router, prefix="/api/checkins")
    return app


@pytest.fixture
async def client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_lookup_success(client, test_settings):
    token = create_access_token("W0NC", "net_control", test_settings)
    mock_result = {
        "callsign": "W0ABC",
        "name": "John Smith",
        "city": "Denver",
        "county": "Denver",
        "state": "CO",
        "country": "United States",
        "latitude": 39.7392,
        "longitude": -104.9903,
        "source": "hamqth",
        "cached": False,
    }

    with patch("backend.modules.checkins.routes.lookup_callsign", return_value=mock_result):
        resp = await client.get("/api/checkins/lookup/W0ABC", cookies={"access_token": token})

    assert resp.status_code == 200
    assert resp.json()["name"] == "John Smith"


@pytest.mark.asyncio
async def test_lookup_not_found(client, test_settings):
    token = create_access_token("W0NC", "net_control", test_settings)

    with patch("backend.modules.checkins.routes.lookup_callsign", return_value=None):
        resp = await client.get("/api/checkins/lookup/XXXXXX", cookies={"access_token": token})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_lookup_viewer_denied(client, test_settings):
    token = create_access_token("KD0TST", "viewer", test_settings)

    resp = await client.get("/api/checkins/lookup/W0ABC", cookies={"access_token": token})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_lookup_admin_allowed(client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    mock_result = {
        "callsign": "W0ABC", "name": "John", "city": "Denver",
        "county": None, "state": "CO", "country": "US",
        "latitude": None, "longitude": None, "source": "qrz", "cached": True,
    }

    with patch("backend.modules.checkins.routes.lookup_callsign", return_value=mock_result):
        resp = await client.get("/api/checkins/lookup/W0ABC", cookies={"access_token": token})

    assert resp.status_code == 200
    assert resp.json()["cached"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_callbook_routes.py -v" shell.nix`
Expected: FAIL — `lookup_callsign` not importable or route doesn't exist

- [ ] **Step 3: Add the lookup route**

Add these imports to the top of `backend/modules/checkins/routes.py`:

```python
from backend.integrations.callbook.service import lookup_callsign
```

Add this route at the end of `backend/modules/checkins/routes.py`:

```python
@checkins_router.get("/lookup/{callsign}")
async def lookup_callsign_route(
    callsign: str,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    result = lookup_callsign(db, callsign)
    if result is None:
        raise HTTPException(status_code=404, detail="Callsign not found in configured callbooks")
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_callbook_routes.py -v" shell.nix`
Expected: All PASS

- [ ] **Step 5: Run all existing tests to make sure nothing broke**

Run: `nix-shell --run "python -m pytest tests/ -v" shell.nix`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/modules/checkins/routes.py tests/test_callbook_routes.py
git commit -m "feat: add callbook lookup endpoint on checkins router"
```

---

### Task 5: Frontend Types and API Client

**Files:**
- Modify: `frontend/src/types/index.ts`
- Create: `frontend/src/api/checkins.ts`

- [ ] **Step 1: Add TypeScript interfaces**

Add to the end of `frontend/src/types/index.ts`:

```typescript
export interface CheckIn {
  id: number;
  session_id: number;
  raw_message_id: number | null;
  callsign: string;
  name: string;
  city: string | null;
  county: string | null;
  state: string | null;
  mode: string;
  comments: string | null;
  latitude: number | null;
  longitude: number | null;
  parse_status: "auto" | "manual_review" | "manually_entered";
  timing_status: "on_time" | "early" | "late";
  is_new_member: boolean;
}

export interface CallbookResult {
  callsign: string;
  name: string | null;
  city: string | null;
  county: string | null;
  state: string | null;
  country: string | null;
  latitude: number | null;
  longitude: number | null;
  source: string;
  cached: boolean;
}
```

- [ ] **Step 2: Create the API client**

Create `frontend/src/api/checkins.ts`:

```typescript
import type { CheckIn, CallbookResult, Session } from "../types";
import { apiFetch } from "./client";

export async function fetchSessionCheckins(sessionId: number): Promise<CheckIn[]> {
  return apiFetch<CheckIn[]>(`/checkins/session/${sessionId}`);
}

export async function scanMailbox(sessionId: number): Promise<{ imported: number; checkins: CheckIn[] }> {
  return apiFetch(`/checkins/scan/${sessionId}`, { method: "POST" });
}

export async function createManualCheckin(data: {
  session_id: number;
  callsign: string;
  name: string;
  mode: string;
  city?: string;
  county?: string;
  state?: string;
  comments?: string;
}): Promise<CheckIn> {
  return apiFetch<CheckIn>("/checkins/manual", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateCheckin(
  checkinId: number,
  data: Partial<Pick<CheckIn, "name" | "callsign" | "city" | "county" | "state" | "mode" | "comments" | "parse_status">>,
): Promise<CheckIn> {
  return apiFetch<CheckIn>(`/checkins/${checkinId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function approveSession(sessionId: number): Promise<{ session_status: string; members_updated: number }> {
  return apiFetch(`/checkins/approve/${sessionId}`, { method: "POST" });
}

export async function lookupCallsign(callsign: string): Promise<CallbookResult> {
  return apiFetch<CallbookResult>(`/checkins/lookup/${callsign}`);
}

export async function fetchRecentSessions(): Promise<Session[]> {
  return apiFetch<Session[]>("/schedule/sessions");
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/checkins.ts
git commit -m "feat: add check-in types and API client"
```

---

### Task 6: CheckInsPage Component

**Files:**
- Create: `frontend/src/pages/CheckInsPage.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create the CheckInsPage component**

Create `frontend/src/pages/CheckInsPage.tsx`:

```tsx
import { useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { AuthContext } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { Spinner } from "../components/Spinner";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { Modal } from "../components/Modal";
import type { CheckIn, CallbookResult, Session, UserRole } from "../types";
import {
  fetchSessionCheckins,
  scanMailbox,
  createManualCheckin,
  updateCheckin,
  approveSession,
  lookupCallsign,
  fetchRecentSessions,
} from "../api/checkins";

const canEdit = (role: UserRole) => role === "admin" || role === "net_control";

const parseStatusBadge: Record<string, { label: string; cls: string }> = {
  auto: { label: "auto", cls: "bg-success/10 text-success border border-success/25" },
  manual_review: { label: "manual review", cls: "bg-warning/15 text-warning border border-warning/30" },
  manually_entered: { label: "manual entry", cls: "bg-accent/10 text-accent border border-accent/25" },
};

const timingBadge: Record<string, { label: string; cls: string }> = {
  on_time: { label: "on time", cls: "bg-success/10 text-success border border-success/25" },
  early: { label: "early", cls: "bg-accent/10 text-accent border border-accent/25" },
  late: { label: "late", cls: "bg-warning/15 text-warning border border-warning/30" },
};

function formatSessionOption(s: Session): string {
  const d = new Date(s.start_date + "T00:00:00");
  const dateStr = d.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
  return `${dateStr} - ${s.session_type.replace(/_/g, " ")} (${s.status})`;
}

function SessionSelector({
  sessions,
  selectedId,
  onChange,
}: {
  sessions: Session[];
  selectedId: number | null;
  onChange: (id: number) => void;
}) {
  return (
    <div className="flex items-center gap-3 mb-4 flex-wrap">
      <label className="text-sm text-text-muted whitespace-nowrap">Session:</label>
      <select
        className="bg-bg-elevated border border-border text-text-primary px-3 py-2 rounded-md text-sm font-mono min-w-[340px] focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
        value={selectedId ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        {sessions.length === 0 && <option value="">No sessions found</option>}
        {sessions.map((s) => (
          <option key={s.id} value={s.id}>
            {formatSessionOption(s)}
          </option>
        ))}
      </select>
      <Link to="/schedule" className="text-sm text-accent hover:text-accent-hover transition-colors">
        Show more...
      </Link>
    </div>
  );
}

function StatsBar({ checkins }: { checkins: CheckIn[] }) {
  const stats = useMemo(() => {
    let needsReview = 0, newMembers = 0, onTime = 0, early = 0, late = 0;
    for (const c of checkins) {
      if (c.parse_status === "manual_review") needsReview++;
      if (c.is_new_member) newMembers++;
      if (c.timing_status === "on_time") onTime++;
      else if (c.timing_status === "early") early++;
      else if (c.timing_status === "late") late++;
    }
    return { total: checkins.length, needsReview, newMembers, onTime, early, late };
  }, [checkins]);

  return (
    <div className="flex gap-6 mb-4 px-4 py-3 bg-bg-surface border border-border rounded-lg flex-wrap">
      <Stat value={stats.total} label="check-ins" />
      <Stat value={stats.needsReview} label="need review" color="text-warning" />
      <Stat value={stats.newMembers} label="new members" color="text-warning" />
      <Stat value={stats.onTime} label="on time" color="text-success" />
      <Stat value={stats.early} label="early" color="text-accent" />
      <Stat value={stats.late} label="late" color="text-warning" />
    </div>
  );
}

function Stat({ value, label, color }: { value: number; label: string; color?: string }) {
  return (
    <div className="flex items-center gap-1.5 text-[0.8125rem]">
      <span className={`font-semibold font-mono ${color || "text-text-primary"}`}>{value}</span>
      <span className="text-text-muted">{label}</span>
    </div>
  );
}

function CheckinTable({
  checkins,
  canEditCheckins,
  onEdit,
}: {
  checkins: CheckIn[];
  canEditCheckins: boolean;
  onEdit: (c: CheckIn) => void;
}) {
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <table className="w-full text-[0.8125rem] border-collapse">
        <thead className="bg-bg-elevated">
          <tr>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Callsign</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Name</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Location</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Mode</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Parse Status</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Timing</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">New</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Comments</th>
            {canEditCheckins && <th className="border-b border-border w-10"></th>}
          </tr>
        </thead>
        <tbody>
          {checkins.map((c) => (
            <tr
              key={c.id}
              className={`border-b border-border last:border-b-0 hover:bg-bg-elevated/50 ${c.parse_status === "manual_review" ? "bg-warning/[0.04]" : ""}`}
            >
              <td className="px-3 py-2.5 font-mono font-semibold text-text-primary">{c.callsign}</td>
              <td className="px-3 py-2.5 text-text-secondary">{c.name}</td>
              <td className="px-3 py-2.5 text-text-secondary">
                {[c.city, c.state].filter(Boolean).join(", ")}
              </td>
              <td className="px-3 py-2.5 text-text-secondary">{c.mode}</td>
              <td className="px-3 py-2.5">
                <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${parseStatusBadge[c.parse_status]?.cls}`}>
                  {parseStatusBadge[c.parse_status]?.label}
                </span>
              </td>
              <td className="px-3 py-2.5">
                <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${timingBadge[c.timing_status]?.cls}`}>
                  {timingBadge[c.timing_status]?.label}
                </span>
              </td>
              <td className="px-3 py-2.5">
                {c.is_new_member && <span className="text-warning" title="New member">&#9733;</span>}
              </td>
              <td className="px-3 py-2.5 max-w-[180px] overflow-hidden text-ellipsis whitespace-nowrap text-text-muted text-xs">
                {c.comments}
              </td>
              {canEditCheckins && (
                <td className="px-3 py-2.5">
                  <button
                    onClick={() => onEdit(c)}
                    className="text-text-muted hover:text-accent transition-colors p-1 rounded"
                    title="Edit"
                  >
                    <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                    </svg>
                  </button>
                </td>
              )}
            </tr>
          ))}
          {checkins.length === 0 && (
            <tr>
              <td colSpan={canEditCheckins ? 9 : 8} className="px-3 py-8 text-center text-text-muted text-sm">
                No check-ins for this session yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function CallsignLookupField({
  value,
  onChange,
  onLookupResult,
}: {
  value: string;
  onChange: (v: string) => void;
  onLookupResult: (result: CallbookResult) => void;
}) {
  const [lookingUp, setLookingUp] = useState(false);
  const [lookupMsg, setLookupMsg] = useState("");

  const handleLookup = async () => {
    if (!value.trim()) return;
    setLookingUp(true);
    setLookupMsg("");
    try {
      const result = await lookupCallsign(value.trim());
      onLookupResult(result);
      setLookupMsg("");
    } catch (err: any) {
      if (err.status === 404) {
        setLookupMsg("Not found in callbook");
      } else if (err.status === 503) {
        setLookupMsg("Callbook not configured");
      } else {
        setLookupMsg("Lookup failed");
      }
    } finally {
      setLookingUp(false);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-text-secondary">Callsign</label>
      <div className="flex gap-2">
        <input
          className="flex-1 rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary font-mono focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
          value={value}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          placeholder="W0ABC"
        />
        <Button size="sm" variant="secondary" onClick={handleLookup} loading={lookingUp} type="button">
          Lookup
        </Button>
      </div>
      {lookupMsg && <p className="text-xs text-warning">{lookupMsg}</p>}
    </div>
  );
}

function AddCheckinModal({
  open,
  onClose,
  sessionId,
  onAdded,
}: {
  open: boolean;
  onClose: () => void;
  sessionId: number;
  onAdded: () => void;
}) {
  const { addToast } = useToast();
  const [form, setForm] = useState({ callsign: "", name: "", mode: "Voice", city: "", county: "", state: "", comments: "" });
  const [saving, setSaving] = useState(false);

  const handleLookupResult = (result: CallbookResult) => {
    setForm((f) => ({
      ...f,
      name: result.name || f.name,
      city: result.city || f.city,
      county: result.county || f.county,
      state: result.state || f.state,
    }));
  };

  const handleSave = async () => {
    if (!form.callsign.trim() || !form.name.trim()) return;
    setSaving(true);
    try {
      await createManualCheckin({
        session_id: sessionId,
        callsign: form.callsign,
        name: form.name,
        mode: form.mode,
        city: form.city || undefined,
        county: form.county || undefined,
        state: form.state || undefined,
        comments: form.comments || undefined,
      });
      addToast("Check-in added", "success");
      setForm({ callsign: "", name: "", mode: "Voice", city: "", county: "", state: "", comments: "" });
      onAdded();
      onClose();
    } catch {
      addToast("Failed to add check-in", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Add Check-in">
      <div className="flex flex-col gap-3">
        <CallsignLookupField value={form.callsign} onChange={(v) => setForm((f) => ({ ...f, callsign: v }))} onLookupResult={handleLookupResult} />
        <Input label="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-text-secondary">Mode</label>
          <select
            className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
            value={form.mode}
            onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value }))}
          >
            <option>Voice</option>
            <option>Winlink</option>
            <option>CW</option>
            <option>Digital</option>
          </select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Input label="City" value={form.city} onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))} />
          <Input label="State" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value }))} />
        </div>
        <Input label="County" value={form.county} onChange={(e) => setForm((f) => ({ ...f, county: e.target.value }))} />
        <Input label="Comments" value={form.comments} onChange={(e) => setForm((f) => ({ ...f, comments: e.target.value }))} />
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSave} loading={saving}>Add Check-in</Button>
        </div>
      </div>
    </Modal>
  );
}

function EditCheckinModal({
  open,
  onClose,
  checkin,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  checkin: CheckIn | null;
  onSaved: () => void;
}) {
  const { addToast } = useToast();
  const [form, setForm] = useState({ callsign: "", name: "", mode: "", city: "", county: "", state: "", comments: "", parse_status: "auto" as CheckIn["parse_status"] });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (checkin) {
      setForm({
        callsign: checkin.callsign,
        name: checkin.name,
        mode: checkin.mode,
        city: checkin.city || "",
        county: checkin.county || "",
        state: checkin.state || "",
        comments: checkin.comments || "",
        parse_status: checkin.parse_status,
      });
    }
  }, [checkin]);

  const handleLookupResult = (result: CallbookResult) => {
    setForm((f) => ({
      ...f,
      name: result.name || f.name,
      city: result.city || f.city,
      county: result.county || f.county,
      state: result.state || f.state,
    }));
  };

  const handleSave = async () => {
    if (!checkin) return;
    setSaving(true);
    try {
      await updateCheckin(checkin.id, {
        callsign: form.callsign,
        name: form.name,
        mode: form.mode,
        city: form.city,
        county: form.county,
        state: form.state,
        comments: form.comments,
        parse_status: form.parse_status,
      });
      addToast("Check-in updated", "success");
      onSaved();
      onClose();
    } catch {
      addToast("Failed to update check-in", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Edit Check-in">
      <div className="flex flex-col gap-3">
        <CallsignLookupField value={form.callsign} onChange={(v) => setForm((f) => ({ ...f, callsign: v }))} onLookupResult={handleLookupResult} />
        <Input label="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        <Input label="Mode" value={form.mode} onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value }))} />
        <div className="grid grid-cols-2 gap-3">
          <Input label="City" value={form.city} onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))} />
          <Input label="State" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value }))} />
        </div>
        <Input label="County" value={form.county} onChange={(e) => setForm((f) => ({ ...f, county: e.target.value }))} />
        <Input label="Comments" value={form.comments} onChange={(e) => setForm((f) => ({ ...f, comments: e.target.value }))} />
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-text-secondary">Parse Status</label>
          <select
            className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
            value={form.parse_status}
            onChange={(e) => setForm((f) => ({ ...f, parse_status: e.target.value as CheckIn["parse_status"] }))}
          >
            <option value="auto">Auto</option>
            <option value="manual_review">Manual Review</option>
            <option value="manually_entered">Manually Entered</option>
          </select>
        </div>
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSave} loading={saving}>Save</Button>
        </div>
      </div>
    </Modal>
  );
}

export function CheckInsPage() {
  const { user } = useContext(AuthContext);
  const { addToast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [checkins, setCheckins] = useState<CheckIn[]>([]);
  const [loading, setLoading] = useState(true);
  const [checkinsLoading, setCheckinsLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [approving, setApproving] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingCheckin, setEditingCheckin] = useState<CheckIn | null>(null);
  const [showApproveConfirm, setShowApproveConfirm] = useState(false);

  const userCanEdit = user ? canEdit(user.role) : false;

  // Load sessions
  useEffect(() => {
    fetchRecentSessions()
      .then((all) => {
        // Sort by start_date desc
        const sorted = [...all].sort((a, b) => b.start_date.localeCompare(a.start_date));

        // Find the next scheduled session
        const now = new Date().toISOString().split("T")[0];
        const nextScheduled = sorted
          .filter((s) => s.status === "scheduled" && s.start_date >= now)
          .pop(); // earliest future scheduled

        // Take 7 most recent
        const recent = sorted.slice(0, 7);

        // Merge: include nextScheduled if not already in recent
        const sessionMap = new Map(recent.map((s) => [s.id, s]));
        if (nextScheduled) sessionMap.set(nextScheduled.id, nextScheduled);

        // Check for ?session= param
        const paramId = searchParams.get("session");
        if (paramId) {
          const paramSession = sorted.find((s) => s.id === Number(paramId));
          if (paramSession) sessionMap.set(paramSession.id, paramSession);
        }

        // Sort final list by date desc
        const finalSessions = [...sessionMap.values()].sort((a, b) => b.start_date.localeCompare(a.start_date));
        setSessions(finalSessions);

        // Select default
        let defaultId: number | null = null;
        if (paramId) {
          defaultId = Number(paramId);
        } else if (nextScheduled) {
          defaultId = nextScheduled.id;
        } else if (finalSessions.length > 0) {
          defaultId = finalSessions[0].id;
        }
        setSelectedSessionId(defaultId);
      })
      .catch(() => addToast("Failed to load sessions", "error"))
      .finally(() => setLoading(false));
  }, []);

  // Load checkins when session changes
  const loadCheckins = useCallback(async () => {
    if (!selectedSessionId) {
      setCheckins([]);
      return;
    }
    setCheckinsLoading(true);
    try {
      const data = await fetchSessionCheckins(selectedSessionId);
      setCheckins(data);
    } catch {
      addToast("Failed to load check-ins", "error");
    } finally {
      setCheckinsLoading(false);
    }
  }, [selectedSessionId, addToast]);

  useEffect(() => {
    loadCheckins();
    // Update selected session object
    const s = sessions.find((s) => s.id === selectedSessionId) || null;
    setSelectedSession(s);
  }, [selectedSessionId, sessions, loadCheckins]);

  const handleSessionChange = (id: number) => {
    setSelectedSessionId(id);
    setSearchParams({ session: String(id) });
  };

  const handleScan = async () => {
    if (!selectedSessionId) return;
    setScanning(true);
    try {
      const result = await scanMailbox(selectedSessionId);
      addToast(`Imported ${result.imported} check-in${result.imported !== 1 ? "s" : ""}`, "success");
      loadCheckins();
    } catch {
      addToast("Scan failed", "error");
    } finally {
      setScanning(false);
    }
  };

  const handleApprove = async () => {
    if (!selectedSessionId) return;
    setApproving(true);
    try {
      const result = await approveSession(selectedSessionId);
      addToast(`Session approved. ${result.members_updated} member records updated.`, "success");
      // Refresh sessions list to get updated status
      const all = await fetchRecentSessions();
      const sorted = [...all].sort((a, b) => b.start_date.localeCompare(a.start_date));
      const sessionMap = new Map(sorted.slice(0, 7).map((s) => [s.id, s]));
      const paramSession = sorted.find((s) => s.id === selectedSessionId);
      if (paramSession) sessionMap.set(paramSession.id, paramSession);
      setSessions([...sessionMap.values()].sort((a, b) => b.start_date.localeCompare(a.start_date)));
      loadCheckins();
    } catch {
      addToast("Approve failed", "error");
    } finally {
      setApproving(false);
      setShowApproveConfirm(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  const isCompleted = selectedSession?.status === "completed";
  const isCancelled = selectedSession?.status === "cancelled";

  return (
    <div>
      <h1 className="text-xl font-bold text-text-primary mb-4">Check-ins</h1>

      <SessionSelector sessions={sessions} selectedId={selectedSessionId} onChange={handleSessionChange} />

      {userCanEdit && selectedSessionId && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <Button size="sm" onClick={handleScan} loading={scanning}>
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            Scan Mailbox
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setShowAddModal(true)}>
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            Add Check-in
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setShowApproveConfirm(true)}
            disabled={isCompleted || isCancelled}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            Approve Session
          </Button>
        </div>
      )}

      {selectedSessionId && !checkinsLoading && <StatsBar checkins={checkins} />}

      {checkinsLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : selectedSessionId ? (
        <CheckinTable checkins={checkins} canEditCheckins={userCanEdit} onEdit={setEditingCheckin} />
      ) : (
        <p className="text-text-muted text-sm py-4">Select a session above to view check-ins.</p>
      )}

      {selectedSessionId && (
        <AddCheckinModal open={showAddModal} onClose={() => setShowAddModal(false)} sessionId={selectedSessionId} onAdded={loadCheckins} />
      )}

      <EditCheckinModal open={editingCheckin !== null} onClose={() => setEditingCheckin(null)} checkin={editingCheckin} onSaved={loadCheckins} />

      <Modal open={showApproveConfirm} onClose={() => setShowApproveConfirm(false)} title="Approve Session">
        <p className="text-sm text-text-secondary mb-4">
          Approve all check-ins and mark this session as completed? This updates member records.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setShowApproveConfirm(false)}>Cancel</Button>
          <Button onClick={handleApprove} loading={approving}>Approve</Button>
        </div>
      </Modal>
    </div>
  );
}
```

- [ ] **Step 2: Wire up the route in App.tsx**

In `frontend/src/App.tsx`:

Replace the `PlaceholderPage` import line to also import `CheckInsPage`:

```typescript
import { CheckInsPage } from "./pages/CheckInsPage";
```

Replace the checkins route line:

```tsx
<Route path="/checkins" element={<ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}><CheckInsPage /></ProtectedRoute>} />
```

- [ ] **Step 3: Verify build compiles**

Run: `cd frontend && nix-shell --run "npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/CheckInsPage.tsx frontend/src/App.tsx
git commit -m "feat: add CheckInsPage component with session selector, table, modals"
```

---

### Task 7: Schedule Page "View Check-ins" Link

**Files:**
- Modify: `frontend/src/pages/SchedulePage.tsx`

- [ ] **Step 1: Add check-in link to each session row**

In `frontend/src/pages/SchedulePage.tsx`, add this import at the top:

```typescript
import { Link } from "react-router-dom";
```

Inside the `ScheduleList` component, in the session card's `<div className="mt-2 flex flex-wrap ...">` section, add a Link after the existing spans:

```tsx
<Link
  to={`/checkins?session=${session.id}`}
  className="text-accent hover:text-accent-hover text-xs transition-colors"
>
  View check-ins
</Link>
```

- [ ] **Step 2: Verify build compiles**

Run: `cd frontend && nix-shell --run "npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/SchedulePage.tsx
git commit -m "feat: add 'View check-ins' link on schedule page session rows"
```

---

### Task 8: Run Full Test Suite

**Files:** None (verification only)

- [ ] **Step 1: Run all backend tests**

Run: `nix-shell --run "python -m pytest tests/ -v" shell.nix`
Expected: All PASS

- [ ] **Step 2: Run frontend type check**

Run: `cd frontend && nix-shell --run "npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors

- [ ] **Step 3: Verify Alembic migration applies cleanly**

Run: `nix-shell --run "alembic upgrade head" shell.nix`
Expected: No errors
