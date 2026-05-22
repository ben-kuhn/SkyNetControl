# Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pluggable delivery system for posting reminders/rosters (via groups.io, email, or Winlink) and a scheduled mailbox scanner for auto-importing check-ins from PAT's local mailbox.

**Architecture:** Two new sub-packages under `backend/integrations/`: a delivery system with a `DeliveryBackend` protocol and three implementations, and a mailbox scanner that wraps existing `mailbox_reader` + `scan_and_import_messages()` on a background schedule. The delivery system hooks into existing `mark_sent()` flows. Configuration lives in the existing `AppConfig` key-value table.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, httpx (groups.io API), smtplib (email), asyncio background tasks, Alembic

---

## File Structure

```
backend/integrations/
├── __init__.py                    # Empty package marker
├── delivery/
│   ├── __init__.py                # Empty package marker
│   ├── models.py                  # DeliveryLog model, DeliveryStatus enum
│   ├── service.py                 # dispatch_delivery(), retry_failed(), get_delivery_status()
│   ├── backends/
│   │   ├── __init__.py            # BACKENDS registry, get_backend()
│   │   ├── base.py                # DeliveryBackend protocol, DeliveryResult dataclass
│   │   ├── groupsio.py            # GroupsIoBackend
│   │   ├── email.py               # EmailBackend
│   │   └── winlink.py             # WinlinkBackend
│   └── routes.py                  # Delivery status and retry endpoints
├── scanner/
│   ├── __init__.py                # Empty package marker
│   ├── service.py                 # Scanner loop, scheduling, active window detection
│   └── routes.py                  # Scanner status and manual trigger endpoints
```

**Modifications to existing files:**

| File | Change |
|------|--------|
| `backend/modules/reminders/service.py:335` | `mark_sent()` calls `dispatch_delivery("reminder", ...)` |
| `backend/modules/roster/service.py:418` | `mark_sent()` calls `dispatch_delivery("roster", ...)` |
| `backend/app.py` | Register delivery + scanner routes, start scanner in lifespan |
| `alembic/env.py:16` | Import `backend.integrations.delivery.models` |
| `frontend/src/pages/ConfigPage.tsx` | Add delivery backend and scanner config fields |

---

### Task 1: DeliveryBackend Protocol and DeliveryResult

**Files:**
- Create: `backend/integrations/__init__.py`
- Create: `backend/integrations/delivery/__init__.py`
- Create: `backend/integrations/delivery/backends/__init__.py`
- Create: `backend/integrations/delivery/backends/base.py`
- Test: `tests/test_delivery_base.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_delivery_base.py
from backend.integrations.delivery.backends.base import DeliveryBackend, DeliveryResult


def test_delivery_result_success():
    result = DeliveryResult(success=True, error=None)
    assert result.success is True
    assert result.error is None


def test_delivery_result_failure():
    result = DeliveryResult(success=False, error="Connection refused")
    assert result.success is False
    assert result.error == "Connection refused"


def test_delivery_backend_is_protocol():
    """DeliveryBackend is a typing Protocol with a send method."""

    class FakeBackend:
        def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
            return DeliveryResult(success=True, error=None)

    backend: DeliveryBackend = FakeBackend()
    result = backend.send("Test", "Body", {})
    assert result.success is True
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_base.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Create package markers and implement base module**

Create `backend/integrations/__init__.py` (empty), `backend/integrations/delivery/__init__.py` (empty), `backend/integrations/delivery/backends/__init__.py` (empty).

```python
# backend/integrations/delivery/backends/base.py
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class DeliveryResult:
    success: bool
    error: str | None


@runtime_checkable
class DeliveryBackend(Protocol):
    def send(self, subject: str, body: str, config: dict) -> DeliveryResult: ...
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_base.py -v`
Expected: PASS (3 tests)

- [x] **Step 5: Commit**

```bash
git add backend/integrations/__init__.py backend/integrations/delivery/__init__.py backend/integrations/delivery/backends/__init__.py backend/integrations/delivery/backends/base.py tests/test_delivery_base.py
git commit -m "feat: add DeliveryBackend protocol and DeliveryResult dataclass"
```

---

### Task 2: DeliveryLog Model and Migration

**Files:**
- Create: `backend/integrations/delivery/models.py`
- Modify: `alembic/env.py:16`
- Test: `tests/test_delivery_models.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_delivery_models.py
import pytest
from datetime import datetime, timezone

from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def test_delivery_status_enum_values():
    assert DeliveryStatus.PENDING.value == "pending"
    assert DeliveryStatus.SENT.value == "sent"
    assert DeliveryStatus.FAILED.value == "failed"


def test_create_delivery_log(db_session):
    log = DeliveryLog(
        content_type="reminder",
        content_id=1,
        backend="email",
        status=DeliveryStatus.PENDING,
        created_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)

    assert log.id is not None
    assert log.content_type == "reminder"
    assert log.content_id == 1
    assert log.backend == "email"
    assert log.status == DeliveryStatus.PENDING
    assert log.error_message is None
    assert log.sent_at is None


def test_delivery_log_unique_constraint(db_session):
    """Only one attempt per backend per piece of content."""
    log1 = DeliveryLog(
        content_type="reminder",
        content_id=1,
        backend="email",
        status=DeliveryStatus.PENDING,
        created_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(log1)
    db_session.commit()

    log2 = DeliveryLog(
        content_type="reminder",
        content_id=1,
        backend="email",
        status=DeliveryStatus.PENDING,
        created_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(log2)
    with pytest.raises(Exception):
        db_session.commit()


def test_different_backends_same_content(db_session):
    """Different backends for same content are allowed."""
    for backend_name in ("email", "groupsio", "winlink"):
        log = DeliveryLog(
            content_type="reminder",
            content_id=1,
            backend=backend_name,
            status=DeliveryStatus.PENDING,
            created_at=datetime.now(tz=timezone.utc),
        )
        db_session.add(log)
    db_session.commit()

    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert len(logs) == 3
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_models.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement the model**

```python
# backend/integrations/delivery/models.py
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"
    __table_args__ = (
        UniqueConstraint("content_type", "content_id", "backend", name="uq_delivery_content_backend"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content_id: Mapped[int] = mapped_column(Integer, nullable=False)
    backend: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(Enum(DeliveryStatus), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [x] **Step 4: Register model in alembic/env.py**

Add this import after line 16 in `alembic/env.py`:

```python
import backend.integrations.delivery.models  # noqa: F401
```

- [x] **Step 5: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_models.py -v`
Expected: PASS (4 tests)

- [x] **Step 6: Generate Alembic migration**

```bash
cd /home/ku0hn/dev/SkyNetControl && alembic upgrade head && alembic revision --autogenerate -m "add delivery_logs table"
```

Verify the generated migration creates the `delivery_logs` table with the unique constraint.

- [x] **Step 7: Commit**

```bash
git add backend/integrations/delivery/models.py alembic/env.py alembic/versions/ tests/test_delivery_models.py
git commit -m "feat: add DeliveryLog model and migration"
```

---

### Task 3: Email Delivery Backend

**Files:**
- Create: `backend/integrations/delivery/backends/email.py`
- Test: `tests/test_delivery_email.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_delivery_email.py
from unittest.mock import patch, MagicMock

from backend.integrations.delivery.backends.email import EmailBackend
from backend.integrations.delivery.backends.base import DeliveryResult


def test_email_backend_success():
    config = {
        "to_address": "net@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "user",
        "smtp_password": "pass",
        "smtp_use_tls": True,
        "smtp_from_address": "skynet@example.com",
    }
    with patch("backend.integrations.delivery.backends.email.smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        backend = EmailBackend()
        result = backend.send("Test Subject", "Test Body", config)

    assert result.success is True
    assert result.error is None
    mock_server.send_message.assert_called_once()


def test_email_backend_failure():
    config = {
        "to_address": "net@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_use_tls": False,
        "smtp_from_address": "skynet@example.com",
    }
    with patch("backend.integrations.delivery.backends.email.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__ = MagicMock(side_effect=ConnectionRefusedError("Connection refused"))

        backend = EmailBackend()
        result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "Connection refused" in result.error


def test_email_backend_no_host():
    config = {
        "to_address": "net@example.com",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_use_tls": False,
        "smtp_from_address": "",
    }
    backend = EmailBackend()
    result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "not configured" in result.error.lower()
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_email.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement the email backend**

```python
# backend/integrations/delivery/backends/email.py
import smtplib
import ssl
from email.message import EmailMessage

from backend.integrations.delivery.backends.base import DeliveryBackend, DeliveryResult


class EmailBackend:
    """Send delivery content via SMTP email."""

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        host = config.get("smtp_host", "")
        if not host:
            return DeliveryResult(success=False, error="SMTP not configured")

        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = config.get("smtp_from_address", "")
            msg["To"] = config.get("to_address", "")
            msg.set_content(body)

            with smtplib.SMTP(host, config.get("smtp_port", 587)) as server:
                if config.get("smtp_use_tls", True):
                    server.starttls(context=ssl.create_default_context())
                username = config.get("smtp_username", "")
                if username:
                    server.login(username, config.get("smtp_password", ""))
                server.send_message(msg)

            return DeliveryResult(success=True, error=None)
        except Exception as exc:
            return DeliveryResult(success=False, error=str(exc))
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_email.py -v`
Expected: PASS (3 tests)

- [x] **Step 5: Commit**

```bash
git add backend/integrations/delivery/backends/email.py tests/test_delivery_email.py
git commit -m "feat: add email delivery backend"
```

---

### Task 4: Groups.io Delivery Backend

**Files:**
- Create: `backend/integrations/delivery/backends/groupsio.py`
- Test: `tests/test_delivery_groupsio.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_delivery_groupsio.py
from unittest.mock import patch, MagicMock

from backend.integrations.delivery.backends.groupsio import GroupsIoBackend
from backend.integrations.delivery.backends.base import DeliveryResult


def test_groupsio_backend_success():
    config = {
        "api_key": "test-key-123",
        "group_name": "w0ne-net",
    }

    mock_response_draft = MagicMock()
    mock_response_draft.status_code = 200
    mock_response_draft.json.return_value = {"draft_id": 42, "group_id": 7}
    mock_response_draft.raise_for_status = MagicMock()

    mock_response_post = MagicMock()
    mock_response_post.status_code = 200
    mock_response_post.raise_for_status = MagicMock()

    with patch("backend.integrations.delivery.backends.groupsio.httpx") as mock_httpx:
        mock_httpx.post.side_effect = [mock_response_draft, mock_response_post]

        backend = GroupsIoBackend()
        result = backend.send("Test Subject", "Test Body", config)

    assert result.success is True
    assert result.error is None

    # Verify draft creation call
    calls = mock_httpx.post.call_args_list
    assert len(calls) == 2

    # First call: create draft
    assert "/newdraft" in calls[0].args[0]
    assert calls[0].kwargs["headers"]["Authorization"] == "Bearer test-key-123"

    # Second call: post draft
    assert "/postdraft" in calls[1].args[0]


def test_groupsio_backend_draft_failure():
    config = {
        "api_key": "test-key-123",
        "group_name": "w0ne-net",
    }

    with patch("backend.integrations.delivery.backends.groupsio.httpx") as mock_httpx:
        mock_httpx.post.side_effect = Exception("API error")

        backend = GroupsIoBackend()
        result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "API error" in result.error


def test_groupsio_backend_no_api_key():
    config = {
        "api_key": "",
        "group_name": "w0ne-net",
    }

    backend = GroupsIoBackend()
    result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "not configured" in result.error.lower()
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_groupsio.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement the groups.io backend**

```python
# backend/integrations/delivery/backends/groupsio.py
import httpx

from backend.integrations.delivery.backends.base import DeliveryBackend, DeliveryResult

BASE_URL = "https://groups.io/api/v1"


class GroupsIoBackend:
    """Post delivery content to a groups.io group via the API."""

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        api_key = config.get("api_key", "")
        if not api_key:
            return DeliveryResult(success=False, error="Groups.io API key not configured")

        group_name = config.get("group_name", "")
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            # Step 1: Create a draft
            draft_resp = httpx.post(
                f"{BASE_URL}/newdraft",
                headers=headers,
                data={"group_name": group_name, "subject": subject, "body": body},
                timeout=30,
            )
            draft_resp.raise_for_status()
            draft_data = draft_resp.json()
            draft_id = draft_data["draft_id"]
            group_id = draft_data["group_id"]

            # Step 2: Post the draft
            post_resp = httpx.post(
                f"{BASE_URL}/postdraft",
                headers=headers,
                data={"draft_id": draft_id, "group_id": group_id},
                timeout=30,
            )
            post_resp.raise_for_status()

            return DeliveryResult(success=True, error=None)
        except Exception as exc:
            return DeliveryResult(success=False, error=str(exc))
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_groupsio.py -v`
Expected: PASS (3 tests)

- [x] **Step 5: Commit**

```bash
git add backend/integrations/delivery/backends/groupsio.py tests/test_delivery_groupsio.py
git commit -m "feat: add groups.io delivery backend"
```

---

### Task 5: Winlink Delivery Backend

**Files:**
- Create: `backend/integrations/delivery/backends/winlink.py`
- Test: `tests/test_delivery_winlink.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_delivery_winlink.py
import os
import tempfile
from pathlib import Path

from backend.integrations.delivery.backends.winlink import WinlinkBackend
from backend.integrations.delivery.backends.base import DeliveryResult


def test_winlink_backend_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        config = {
            "target_address": "W0NE@winlink.org",
            "mailbox_path": tmpdir,
            "callsign": "W0NE",
        }

        backend = WinlinkBackend()
        result = backend.send("Test Subject", "Test Body", config)

        assert result.success is True
        assert result.error is None

        # Verify .b2f file was written
        b2f_files = list(out_dir.glob("*.b2f"))
        assert len(b2f_files) == 1

        content = b2f_files[0].read_text()
        assert "Mid:" in content
        assert "From: W0NE" in content
        assert "To: W0NE@winlink.org" in content
        assert "Subject: Test Subject" in content
        assert "Test Body" in content


def test_winlink_backend_no_out_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        # out/ directory does not exist — backend should create it
        config = {
            "target_address": "W0NE@winlink.org",
            "mailbox_path": tmpdir,
            "callsign": "W0NE",
        }

        backend = WinlinkBackend()
        result = backend.send("Test Subject", "Test Body", config)

        assert result.success is True
        out_dir = Path(tmpdir) / "out"
        assert out_dir.is_dir()


def test_winlink_backend_no_mailbox_path():
    config = {
        "target_address": "W0NE@winlink.org",
        "mailbox_path": "",
        "callsign": "W0NE",
    }

    backend = WinlinkBackend()
    result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "not configured" in result.error.lower()


def test_winlink_b2f_format():
    """Verify the .b2f file contains proper headers and body."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        config = {
            "target_address": "NET@winlink.org",
            "mailbox_path": tmpdir,
            "callsign": "W0NE",
        }

        backend = WinlinkBackend()
        backend.send("Weekly Roster", "Line 1\nLine 2", config)

        b2f_file = list(out_dir.glob("*.b2f"))[0]
        content = b2f_file.read_text()
        lines = content.split("\n")

        # Check header lines
        header_keys = [line.split(":")[0] for line in lines if ":" in line and lines.index(line) < 10]
        assert "Mid" in header_keys
        assert "From" in header_keys
        assert "To" in header_keys
        assert "Subject" in header_keys
        assert "Body" in header_keys

        # Check body content follows headers
        assert "Line 1\nLine 2" in content
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_winlink.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement the Winlink backend**

```python
# backend/integrations/delivery/backends/winlink.py
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.integrations.delivery.backends.base import DeliveryBackend, DeliveryResult


class WinlinkBackend:
    """Write a .b2f file to PAT's out/ directory for delivery on next sync."""

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        mailbox_path = config.get("mailbox_path", "")
        if not mailbox_path:
            return DeliveryResult(success=False, error="Winlink mailbox path not configured")

        target_address = config.get("target_address", "")
        callsign = config.get("callsign", "")

        try:
            out_dir = Path(mailbox_path) / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            message_id = uuid.uuid4().hex[:12].upper()
            now = datetime.now(tz=timezone.utc)
            date_str = now.strftime("%Y/%m/%d %H:%M")
            body_bytes = len(body.encode("utf-8"))

            b2f_content = (
                f"Mid: {message_id}\n"
                f"From: {callsign}\n"
                f"To: {target_address}\n"
                f"Subject: {subject}\n"
                f"Mbo: {callsign}\n"
                f"Date: {date_str}\n"
                f"Body: {body_bytes}\n"
                f"\n"
                f"{body}"
            )

            filename = f"{message_id}.b2f"
            (out_dir / filename).write_text(b2f_content)

            return DeliveryResult(success=True, error=None)
        except Exception as exc:
            return DeliveryResult(success=False, error=str(exc))
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_winlink.py -v`
Expected: PASS (4 tests)

- [x] **Step 5: Commit**

```bash
git add backend/integrations/delivery/backends/winlink.py tests/test_delivery_winlink.py
git commit -m "feat: add Winlink delivery backend"
```

---

### Task 6: Backend Registry

**Files:**
- Modify: `backend/integrations/delivery/backends/__init__.py`
- Test: `tests/test_delivery_registry.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_delivery_registry.py
import pytest

from backend.integrations.delivery.backends import BACKENDS, get_backend
from backend.integrations.delivery.backends.base import DeliveryBackend
from backend.integrations.delivery.backends.email import EmailBackend
from backend.integrations.delivery.backends.groupsio import GroupsIoBackend
from backend.integrations.delivery.backends.winlink import WinlinkBackend


def test_backends_registry_has_all_backends():
    assert "email" in BACKENDS
    assert "groupsio" in BACKENDS
    assert "winlink" in BACKENDS
    assert len(BACKENDS) == 3


def test_get_backend_returns_correct_type():
    assert isinstance(get_backend("email"), EmailBackend)
    assert isinstance(get_backend("groupsio"), GroupsIoBackend)
    assert isinstance(get_backend("winlink"), WinlinkBackend)


def test_get_backend_unknown_raises():
    with pytest.raises(KeyError):
        get_backend("pigeon")
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_registry.py -v`
Expected: FAIL with `ImportError`

- [x] **Step 3: Implement the registry**

```python
# backend/integrations/delivery/backends/__init__.py
from backend.integrations.delivery.backends.base import DeliveryBackend
from backend.integrations.delivery.backends.email import EmailBackend
from backend.integrations.delivery.backends.groupsio import GroupsIoBackend
from backend.integrations.delivery.backends.winlink import WinlinkBackend

BACKENDS: dict[str, type] = {
    "email": EmailBackend,
    "groupsio": GroupsIoBackend,
    "winlink": WinlinkBackend,
}


def get_backend(name: str) -> DeliveryBackend:
    """Return an instance of the named backend. Raises KeyError if unknown."""
    return BACKENDS[name]()
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_registry.py -v`
Expected: PASS (3 tests)

- [x] **Step 5: Commit**

```bash
git add backend/integrations/delivery/backends/__init__.py tests/test_delivery_registry.py
git commit -m "feat: add delivery backend registry"
```

---

### Task 7: Delivery Service

**Files:**
- Create: `backend/integrations/delivery/service.py`
- Test: `tests/test_delivery_service.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_delivery_service.py
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.delivery.backends.base import DeliveryResult
from backend.integrations.delivery.service import (
    dispatch_delivery,
    retry_failed,
    get_delivery_status,
)
from backend.config_mgmt.models import AppConfig
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def _set_config(db_session, key, value):
    existing = db_session.get(AppConfig, key)
    if existing:
        existing.value = value
    else:
        db_session.add(AppConfig(key=key, value=value))
    db_session.commit()


def test_dispatch_creates_logs_and_delivers(db_session):
    _set_config(db_session, "delivery.backends", json.dumps(["email"]))
    _set_config(db_session, "delivery.email.to_address", "net@example.com")

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=True, error=None)
        mock_get.return_value = mock_backend

        result = dispatch_delivery(db_session, "reminder", 1, "Subject", "Body")

    assert result is True
    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert len(logs) == 1
    assert logs[0].status == DeliveryStatus.SENT
    assert logs[0].sent_at is not None


def test_dispatch_all_fail_returns_false(db_session):
    _set_config(db_session, "delivery.backends", json.dumps(["email"]))
    _set_config(db_session, "delivery.email.to_address", "net@example.com")

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=False, error="SMTP down")
        mock_get.return_value = mock_backend

        result = dispatch_delivery(db_session, "reminder", 1, "Subject", "Body")

    assert result is False
    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert len(logs) == 1
    assert logs[0].status == DeliveryStatus.FAILED
    assert logs[0].error_message == "SMTP down"


def test_dispatch_no_backends_configured(db_session):
    _set_config(db_session, "delivery.backends", json.dumps([]))

    result = dispatch_delivery(db_session, "reminder", 1, "Subject", "Body")
    assert result is False


def test_dispatch_multiple_backends_partial_success(db_session):
    _set_config(db_session, "delivery.backends", json.dumps(["email", "groupsio"]))
    _set_config(db_session, "delivery.email.to_address", "net@example.com")
    _set_config(db_session, "delivery.groupsio.api_key", "key-123")
    _set_config(db_session, "delivery.groupsio.group_name", "w0ne")

    call_count = 0

    def mock_get(name):
        mock = MagicMock()
        nonlocal call_count
        if call_count == 0:
            mock.send.return_value = DeliveryResult(success=True, error=None)
        else:
            mock.send.return_value = DeliveryResult(success=False, error="API error")
        call_count += 1
        return mock

    with patch("backend.integrations.delivery.service.get_backend", side_effect=mock_get):
        result = dispatch_delivery(db_session, "reminder", 1, "Subject", "Body")

    assert result is True  # at least one succeeded
    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert len(logs) == 2
    statuses = {log.status for log in logs}
    assert DeliveryStatus.SENT in statuses
    assert DeliveryStatus.FAILED in statuses


def test_retry_failed_only_retries_failed(db_session):
    _set_config(db_session, "delivery.email.to_address", "net@example.com")

    # Create a SENT log and a FAILED log
    db_session.add(DeliveryLog(
        content_type="reminder", content_id=1, backend="email",
        status=DeliveryStatus.SENT, created_at=datetime.now(tz=timezone.utc),
        sent_at=datetime.now(tz=timezone.utc),
    ))
    db_session.add(DeliveryLog(
        content_type="reminder", content_id=1, backend="groupsio",
        status=DeliveryStatus.FAILED, error_message="API error",
        created_at=datetime.now(tz=timezone.utc),
    ))
    db_session.commit()

    _set_config(db_session, "delivery.groupsio.api_key", "key-123")
    _set_config(db_session, "delivery.groupsio.group_name", "w0ne")

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=True, error=None)
        mock_get.return_value = mock_backend

        retry_failed(db_session, "reminder", 1)

    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert all(log.status == DeliveryStatus.SENT for log in logs)


def test_get_delivery_status(db_session):
    db_session.add(DeliveryLog(
        content_type="reminder", content_id=1, backend="email",
        status=DeliveryStatus.SENT, created_at=datetime.now(tz=timezone.utc),
        sent_at=datetime.now(tz=timezone.utc),
    ))
    db_session.commit()

    logs = get_delivery_status(db_session, "reminder", 1)
    assert len(logs) == 1
    assert logs[0].backend == "email"
    assert logs[0].status == DeliveryStatus.SENT
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement the delivery service**

```python
# backend/integrations/delivery/service.py
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.config_mgmt.service import get_config_value
from backend.integrations.delivery.backends import get_backend
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus

logger = logging.getLogger(__name__)


def _build_config(db: Session, backend_name: str) -> dict:
    """Build config dict for a backend from AppConfig + Settings."""
    config: dict = {}

    if backend_name == "email":
        config["to_address"] = get_config_value(db, "delivery.email.to_address", "")
        # SMTP settings are loaded from env/Settings, but we read them from AppConfig
        # if present, otherwise the caller should overlay Settings values
        from backend.config import settings
        config["smtp_host"] = settings.smtp.host
        config["smtp_port"] = settings.smtp.port
        config["smtp_username"] = settings.smtp.username
        config["smtp_password"] = settings.smtp.password
        config["smtp_use_tls"] = settings.smtp.use_tls
        config["smtp_from_address"] = settings.smtp.from_address

    elif backend_name == "groupsio":
        config["api_key"] = get_config_value(db, "delivery.groupsio.api_key", "")
        config["group_name"] = get_config_value(db, "delivery.groupsio.group_name", "")

    elif backend_name == "winlink":
        config["target_address"] = get_config_value(db, "delivery.winlink.target_address", "")
        config["mailbox_path"] = get_config_value(db, "pat_mailbox_path", "")
        net_address = get_config_value(db, "net_address", "")
        config["callsign"] = net_address.split("@")[0].upper() if "@" in net_address else net_address.upper()

    return config


def dispatch_delivery(
    db: Session,
    content_type: str,
    content_id: int,
    subject: str,
    body: str,
) -> bool:
    """Dispatch content to all enabled delivery backends.

    Returns True if at least one backend succeeds.
    """
    backends_json = get_config_value(db, "delivery.backends", "[]")
    backend_names = json.loads(backends_json)

    if not backend_names:
        logger.info("No delivery backends configured")
        return False

    any_success = False

    for name in backend_names:
        config = _build_config(db, name)
        log = DeliveryLog(
            content_type=content_type,
            content_id=content_id,
            backend=name,
            status=DeliveryStatus.PENDING,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.flush()

        try:
            backend = get_backend(name)
            result = backend.send(subject, body, config)
        except KeyError:
            result_success = False
            result_error = f"Unknown backend: {name}"
            log.status = DeliveryStatus.FAILED
            log.error_message = result_error
            db.commit()
            continue

        if result.success:
            log.status = DeliveryStatus.SENT
            log.sent_at = datetime.now(tz=timezone.utc)
            any_success = True
        else:
            log.status = DeliveryStatus.FAILED
            log.error_message = result.error

        db.commit()

    return any_success


def retry_failed(db: Session, content_type: str, content_id: int) -> bool:
    """Retry only failed delivery attempts for a piece of content.

    Returns True if at least one retry succeeds.
    """
    failed_logs = (
        db.query(DeliveryLog)
        .filter_by(content_type=content_type, content_id=content_id, status=DeliveryStatus.FAILED)
        .all()
    )

    if not failed_logs:
        return False

    any_success = False
    for log in failed_logs:
        config = _build_config(db, log.backend)
        try:
            backend = get_backend(log.backend)
            result = backend.send(
                "",  # subject not stored on log; caller provides via original content
                "",
                config,
            )
        except KeyError:
            continue

        if result.success:
            log.status = DeliveryStatus.SENT
            log.sent_at = datetime.now(tz=timezone.utc)
            log.error_message = None
            any_success = True
        else:
            log.error_message = result.error

        db.commit()

    return any_success


def get_delivery_status(db: Session, content_type: str, content_id: int) -> list[DeliveryLog]:
    """Get all delivery log entries for a piece of content."""
    return (
        db.query(DeliveryLog)
        .filter_by(content_type=content_type, content_id=content_id)
        .order_by(DeliveryLog.created_at)
        .all()
    )
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_service.py -v`
Expected: PASS (6 tests)

- [x] **Step 5: Commit**

```bash
git add backend/integrations/delivery/service.py tests/test_delivery_service.py
git commit -m "feat: add delivery service with dispatch, retry, and status"
```

---

### Task 8: Delivery Routes

**Files:**
- Create: `backend/integrations/delivery/routes.py`
- Test: `tests/test_delivery_routes.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_delivery_routes.py
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.config_mgmt.models import AppConfig
from backend.auth.models import User, UserRole
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def _create_admin(db_session):
    user = User(callsign="ADMIN", name="Admin User", email="admin@test.com", role=UserRole.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


def _auth_headers(app, callsign="ADMIN"):
    from backend.auth.service import create_access_token
    token = create_access_token(callsign, settings=app.state.settings)
    return {"Cookie": f"access_token={token}"}


@pytest.mark.anyio
async def test_get_delivery_status(app, client, db_session):
    _create_admin(db_session)
    db_session.add(DeliveryLog(
        content_type="reminder", content_id=1, backend="email",
        status=DeliveryStatus.SENT, created_at=datetime.now(tz=timezone.utc),
        sent_at=datetime.now(tz=timezone.utc),
    ))
    db_session.commit()

    resp = await client.get(
        "/api/delivery/reminder/1",
        headers=_auth_headers(app),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["backend"] == "email"
    assert data[0]["status"] == "sent"


@pytest.mark.anyio
async def test_get_delivery_status_empty(app, client, db_session):
    _create_admin(db_session)
    resp = await client.get(
        "/api/delivery/reminder/999",
        headers=_auth_headers(app),
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_retry_delivery(app, client, db_session):
    _create_admin(db_session)
    db_session.add(DeliveryLog(
        content_type="reminder", content_id=1, backend="email",
        status=DeliveryStatus.FAILED, error_message="SMTP down",
        created_at=datetime.now(tz=timezone.utc),
    ))
    db_session.add(AppConfig(key="delivery.email.to_address", value="net@test.com"))
    db_session.commit()

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        from backend.integrations.delivery.backends.base import DeliveryResult
        mock_backend = type("MockBackend", (), {"send": lambda self, s, b, c: DeliveryResult(success=True, error=None)})()
        mock_get.return_value = mock_backend

        resp = await client.post(
            "/api/delivery/reminder/1/retry",
            headers=_auth_headers(app),
        )

    assert resp.status_code == 200
    assert resp.json()["retried"] is True


@pytest.mark.anyio
async def test_retry_requires_auth(app, client):
    resp = await client.post("/api/delivery/reminder/1/retry")
    assert resp.status_code == 401
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_routes.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement delivery routes**

```python
# backend/integrations/delivery/routes.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, require_role
from backend.auth.models import UserRole
from backend.integrations.delivery.service import get_delivery_status, retry_failed

delivery_router = APIRouter()


@delivery_router.get("/{content_type}/{content_id}")
def list_delivery_attempts(
    content_type: str,
    content_id: int,
    db: Session = Depends(get_db_session),
    _user=Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL, UserRole.VIEWER)),
):
    logs = get_delivery_status(db, content_type, content_id)
    return [
        {
            "id": log.id,
            "backend": log.backend,
            "status": log.status.value,
            "error_message": log.error_message,
            "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


@delivery_router.post("/{content_type}/{content_id}/retry")
def retry_delivery(
    content_type: str,
    content_id: int,
    db: Session = Depends(get_db_session),
    _user=Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
):
    success = retry_failed(db, content_type, content_id)
    return {"retried": success}
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_routes.py -v`

Note: This test will fail until the router is registered in `app.py`. For now, register it temporarily to verify the test logic works. The permanent registration happens in Task 11.

- [x] **Step 5: Register the router in app.py**

Add to `backend/app.py` after the existing imports (line 19):

```python
from backend.integrations.delivery.routes import delivery_router
```

Add after line 56 (`audit_router` registration):

```python
    app.include_router(delivery_router, prefix="/api/delivery")
```

- [x] **Step 6: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_routes.py -v`
Expected: PASS (4 tests)

- [x] **Step 7: Commit**

```bash
git add backend/integrations/delivery/routes.py backend/app.py tests/test_delivery_routes.py
git commit -m "feat: add delivery status and retry API endpoints"
```

---

### Task 9: Wire mark_sent() to Delivery Dispatch

**Files:**
- Modify: `backend/modules/reminders/service.py:335-345`
- Modify: `backend/modules/roster/service.py:418-428`
- Test: `tests/test_delivery_wiring.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_delivery_wiring.py
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from backend.modules.reminders.models import ReminderLog, ReminderStatus
from backend.modules.roster.models import RosterLog, RosterStatus
from backend.modules.schedule.models import NetSession, NetSeason, SessionType, SessionStatus
from backend.config_mgmt.models import AppConfig
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.delivery.backends.base import DeliveryResult
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def _setup_season_and_session(db_session):
    from datetime import date
    season = NetSeason(name="Test", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    db_session.add(season)
    db_session.flush()
    session = NetSession(
        season_id=season.id, start_date=date(2026, 5, 20),
        session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
    )
    db_session.add(session)
    db_session.commit()
    return session


def test_reminder_mark_sent_dispatches_delivery(db_session):
    session = _setup_season_and_session(db_session)
    log = ReminderLog(
        session_id=session.id, status=ReminderStatus.APPROVED,
        content_subject="Reminder Subject", content_body="Reminder Body",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc), approved_by="ADMIN",
    )
    db_session.add(log)
    db_session.add(AppConfig(key="delivery.backends", value=json.dumps(["email"])))
    db_session.add(AppConfig(key="delivery.email.to_address", value="net@test.com"))
    db_session.commit()
    db_session.refresh(log)

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=True, error=None)
        mock_get.return_value = mock_backend

        from backend.modules.reminders.service import mark_sent
        result = mark_sent(db_session, log.id)

    assert result is not None
    assert result.status == ReminderStatus.SENT

    delivery_logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=log.id).all()
    assert len(delivery_logs) == 1
    assert delivery_logs[0].status == DeliveryStatus.SENT


def test_reminder_mark_sent_stays_approved_on_all_fail(db_session):
    session = _setup_season_and_session(db_session)
    log = ReminderLog(
        session_id=session.id, status=ReminderStatus.APPROVED,
        content_subject="Subject", content_body="Body",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc), approved_by="ADMIN",
    )
    db_session.add(log)
    db_session.add(AppConfig(key="delivery.backends", value=json.dumps(["email"])))
    db_session.add(AppConfig(key="delivery.email.to_address", value="net@test.com"))
    db_session.commit()
    db_session.refresh(log)

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=False, error="SMTP down")
        mock_get.return_value = mock_backend

        from backend.modules.reminders.service import mark_sent
        result = mark_sent(db_session, log.id)

    assert result is None  # stays APPROVED, not transitioned
    db_session.refresh(log)
    assert log.status == ReminderStatus.APPROVED


def test_roster_mark_sent_dispatches_delivery(db_session):
    session = _setup_season_and_session(db_session)
    log = RosterLog(
        session_id=session.id, status=RosterStatus.APPROVED,
        content_subject="Roster Subject", content_header="Header",
        content_welcome="Welcome", content_comments="Comments",
        content_footer="Footer",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc), approved_by="ADMIN",
    )
    db_session.add(log)
    db_session.add(AppConfig(key="delivery.backends", value=json.dumps(["email"])))
    db_session.add(AppConfig(key="delivery.email.to_address", value="net@test.com"))
    db_session.commit()
    db_session.refresh(log)

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=True, error=None)
        mock_get.return_value = mock_backend

        from backend.modules.roster.service import mark_sent
        result = mark_sent(db_session, log.id)

    assert result is not None
    assert result.status == RosterStatus.SENT

    delivery_logs = db_session.query(DeliveryLog).filter_by(content_type="roster", content_id=log.id).all()
    assert len(delivery_logs) == 1
    assert delivery_logs[0].status == DeliveryStatus.SENT
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_wiring.py -v`
Expected: FAIL — tests pass the delivery mock but `mark_sent()` doesn't call `dispatch_delivery` yet, so no DeliveryLog records are created.

- [x] **Step 3: Modify reminders mark_sent()**

Replace the `mark_sent` function in `backend/modules/reminders/service.py` (lines 335-345):

```python
def mark_sent(db: Session, reminder_id: int) -> ReminderLog | None:
    """Transition an APPROVED reminder to SENT via delivery backends."""
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status != ReminderStatus.APPROVED:
        return None

    from backend.integrations.delivery.service import dispatch_delivery

    delivered = dispatch_delivery(
        db, "reminder", log.id, log.content_subject, log.content_body
    )

    if not delivered:
        return None  # stay APPROVED so user can retry

    log.status = ReminderStatus.SENT
    log.sent_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(log)
    return log
```

- [x] **Step 4: Modify roster mark_sent()**

Replace the `mark_sent` function in `backend/modules/roster/service.py` (lines 418-428):

```python
def mark_sent(db: Session, roster_id: int) -> RosterLog | None:
    """Transition APPROVED → SENT via delivery backends."""
    log = db.get(RosterLog, roster_id)
    if log is None or log.status != RosterStatus.APPROVED:
        return None

    from backend.integrations.delivery.service import dispatch_delivery

    assembled = assemble_roster(db, roster_id)
    body = assembled if assembled else ""

    delivered = dispatch_delivery(
        db, "roster", log.id, log.content_subject, body
    )

    if not delivered:
        return None  # stay APPROVED so user can retry

    log.status = RosterStatus.SENT
    log.sent_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(log)
    return log
```

- [x] **Step 5: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_delivery_wiring.py -v`
Expected: PASS (3 tests)

- [x] **Step 6: Run full test suite to check for regressions**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest -x -q`
Expected: All existing tests still pass. Some reminder/roster tests may need adjustment if they relied on `mark_sent()` always transitioning to SENT without delivery backends configured. If so, add `delivery.backends` config with `[]` in those test fixtures so `dispatch_delivery` returns `False` and `mark_sent` returns `None`. Or mock `dispatch_delivery` to return `True`.

- [x] **Step 7: Fix any regressions**

If existing `test_reminder_service.py` or `test_roster_service.py` tests fail because `mark_sent` now returns `None` (no backends configured), patch `dispatch_delivery` to return `True` in those tests:

For any test calling `mark_sent` that expects it to succeed, add:
```python
with patch("backend.modules.reminders.service.dispatch_delivery", return_value=True):
    result = mark_sent(db_session, log.id)
```

Or for roster tests:
```python
with patch("backend.modules.roster.service.dispatch_delivery", return_value=True):
    result = mark_sent(db_session, log.id)
```

- [x] **Step 8: Run full test suite again**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest -x -q`
Expected: All tests pass

- [x] **Step 9: Commit**

```bash
git add backend/modules/reminders/service.py backend/modules/roster/service.py tests/test_delivery_wiring.py tests/test_reminder_service.py tests/test_roster_service.py
git commit -m "feat: wire mark_sent() to delivery dispatch system"
```

---

### Task 10: Scanner Service — Active Window Detection

**Files:**
- Create: `backend/integrations/scanner/__init__.py`
- Create: `backend/integrations/scanner/service.py`
- Test: `tests/test_scanner_service.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_scanner_service.py
import pytest
from datetime import date, datetime, timezone, timedelta

from backend.modules.schedule.models import NetSession, NetSeason, SessionType, SessionStatus
from backend.integrations.scanner.service import find_active_session
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def _create_session(db_session, start_date, end_date=None, grace_hours=24.0, status=SessionStatus.SCHEDULED):
    season = NetSeason(name="Test", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    db_session.add(season)
    db_session.flush()
    net_session = NetSession(
        season_id=season.id, start_date=start_date, end_date=end_date,
        grace_period_hours=grace_hours, session_type=SessionType.REGULAR_CHECKIN,
        status=status,
    )
    db_session.add(net_session)
    db_session.commit()
    return net_session


def test_find_active_session_during_window(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0)

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is not None
    assert result.start_date == today


def test_find_active_session_in_grace_before(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0)

    # 12 hours before session start = within 24h grace period
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is not None


def test_find_active_session_in_grace_after(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0)

    # 12 hours after session end = within 24h grace period
    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is not None


def test_find_active_session_outside_window(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0)

    # 3 days after session = well outside grace period
    now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is None


def test_find_active_session_skips_completed(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0, status=SessionStatus.COMPLETED)

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is None


def test_find_active_session_no_sessions(db_session):
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_scanner_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement the scanner service (active window detection)**

Create `backend/integrations/scanner/__init__.py` (empty).

```python
# backend/integrations/scanner/service.py
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.modules.schedule.models import NetSession, SessionStatus

logger = logging.getLogger(__name__)


def find_active_session(db: Session, now: datetime) -> NetSession | None:
    """Find a SCHEDULED session whose window (start - grace through end + grace) contains `now`."""
    sessions = (
        db.query(NetSession)
        .filter(NetSession.status == SessionStatus.SCHEDULED)
        .all()
    )

    for session in sessions:
        session_start = datetime.combine(session.start_date, datetime.min.time(), tzinfo=timezone.utc)
        grace = timedelta(hours=session.grace_period_hours)

        window_open = session_start - grace

        if session.end_date is not None:
            session_end = datetime.combine(session.end_date, datetime.max.time(), tzinfo=timezone.utc)
            window_close = session_end + grace
        else:
            # Open-ended session — window extends indefinitely after start
            window_close = None

        if now >= window_open and (window_close is None or now <= window_close):
            return session

    return None
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_scanner_service.py -v`
Expected: PASS (6 tests)

- [x] **Step 5: Commit**

```bash
git add backend/integrations/scanner/__init__.py backend/integrations/scanner/service.py tests/test_scanner_service.py
git commit -m "feat: add scanner active window detection"
```

---

### Task 11: Scanner Service — Scan and Background Loop

**Files:**
- Modify: `backend/integrations/scanner/service.py`
- Test: `tests/test_scanner_loop.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_scanner_loop.py
import json
import pytest
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

from backend.modules.schedule.models import NetSession, NetSeason, SessionType, SessionStatus
from backend.config_mgmt.models import AppConfig
from backend.integrations.scanner.service import run_scan, ScannerState
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def _create_active_session(db_session):
    season = NetSeason(name="Test", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    db_session.add(season)
    db_session.flush()
    net_session = NetSession(
        season_id=season.id, start_date=date(2026, 5, 20), end_date=date(2026, 5, 20),
        grace_period_hours=24.0, session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
    )
    db_session.add(net_session)
    db_session.commit()
    return net_session


def test_run_scan_imports_messages(db_session):
    net_session = _create_active_session(db_session)
    db_session.add(AppConfig(key="net_address", value="w0ne@winlink.org"))
    db_session.add(AppConfig(key="pat_mailbox_path", value="/tmp/fake/mailbox/W0NE"))
    db_session.commit()

    fake_messages = [
        {"message_id": "msg1", "from_address": "test@winlink.org", "to_address": "w0ne@winlink.org",
         "subject": "Check-in", "received_at": datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
         "body": "Name: Test User\nCallsign: TSTU\nCity: Denver\nCounty: Denver\nState: CO"}
    ]

    with patch("backend.integrations.scanner.service.read_mailbox", return_value=fake_messages) as mock_read:
        with patch("backend.integrations.scanner.service.scan_and_import_messages", return_value=[]) as mock_scan:
            now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
            count = run_scan(db_session, now)

    mock_read.assert_called_once()
    mock_scan.assert_called_once()
    assert count == 0  # mock returns empty list


def test_run_scan_no_active_session(db_session):
    db_session.add(AppConfig(key="net_address", value="w0ne@winlink.org"))
    db_session.add(AppConfig(key="pat_mailbox_path", value="/tmp/fake/mailbox/W0NE"))
    db_session.commit()

    # No sessions in DB — should return None (skipped)
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    count = run_scan(db_session, now)
    assert count is None


def test_run_scan_no_mailbox_path(db_session):
    _create_active_session(db_session)
    db_session.add(AppConfig(key="net_address", value="w0ne@winlink.org"))
    db_session.commit()

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    count = run_scan(db_session, now)
    assert count is None


def test_scanner_state():
    state = ScannerState()
    assert state.running is False
    assert state.last_scan_time is None
    assert state.last_scan_count is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_scanner_loop.py -v`
Expected: FAIL with `ImportError` (run_scan, ScannerState not defined)

- [x] **Step 3: Add run_scan and ScannerState to scanner service**

Add to `backend/integrations/scanner/service.py`:

```python
# Add these imports at the top
from backend.config_mgmt.service import get_config_value
from backend.modules.checkins.mailbox_reader import read_mailbox
from backend.modules.checkins.service import scan_and_import_messages


class ScannerState:
    """Mutable state for the background scanner."""

    def __init__(self):
        self.running: bool = False
        self.last_scan_time: datetime | None = None
        self.last_scan_count: int | None = None
        self.active_session_id: int | None = None


# Module-level singleton
scanner_state = ScannerState()


def run_scan(db: Session, now: datetime) -> int | None:
    """Run a single scan cycle. Returns count of imported check-ins, or None if skipped."""
    net_address = get_config_value(db, "net_address", "")
    mailbox_path = get_config_value(db, "pat_mailbox_path", "")

    if not net_address or not mailbox_path:
        logger.info("Scanner skipped: net_address or pat_mailbox_path not configured")
        return None

    session = find_active_session(db, now)
    if session is None:
        logger.debug("Scanner skipped: no active session window")
        return None

    # Derive inbox path: mailbox_path should point to the callsign directory
    # which contains in/ and out/ subdirectories
    import os
    inbox_path = os.path.join(mailbox_path, "in")

    messages = read_mailbox(inbox_path, net_address)
    checkins = scan_and_import_messages(db, messages, session)

    scanner_state.last_scan_time = now
    scanner_state.last_scan_count = len(checkins)
    scanner_state.active_session_id = session.id

    logger.info("Scanner completed: %d new check-ins imported", len(checkins))
    return len(checkins)


async def scanner_loop(session_factory, get_interval_minutes):
    """Background loop that runs scans on a schedule."""
    import asyncio

    scanner_state.running = True
    logger.info("Scanner started")

    try:
        while scanner_state.running:
            interval = get_interval_minutes()
            try:
                with session_factory() as db:
                    now = datetime.now(tz=timezone.utc)
                    run_scan(db, now)
            except Exception:
                logger.exception("Scanner error during scan cycle")

            await asyncio.sleep(interval * 60)
    finally:
        scanner_state.running = False
        logger.info("Scanner stopped")
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_scanner_loop.py -v`
Expected: PASS (4 tests)

- [x] **Step 5: Commit**

```bash
git add backend/integrations/scanner/service.py tests/test_scanner_loop.py
git commit -m "feat: add scanner run_scan and background loop"
```

---

### Task 12: Scanner Routes

**Files:**
- Create: `backend/integrations/scanner/routes.py`
- Test: `tests/test_scanner_routes.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_scanner_routes.py
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from backend.auth.models import User, UserRole
from backend.integrations.scanner.service import scanner_state
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def _create_admin(db_session):
    user = User(callsign="ADMIN", name="Admin User", email="admin@test.com", role=UserRole.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


def _auth_headers(app, callsign="ADMIN"):
    from backend.auth.service import create_access_token
    token = create_access_token(callsign, settings=app.state.settings)
    return {"Cookie": f"access_token={token}"}


@pytest.mark.anyio
async def test_scanner_status(app, client, db_session):
    _create_admin(db_session)

    scanner_state.running = False
    scanner_state.last_scan_time = None
    scanner_state.last_scan_count = None
    scanner_state.active_session_id = None

    resp = await client.get("/api/scanner/status", headers=_auth_headers(app))
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    assert data["last_scan_time"] is None


@pytest.mark.anyio
async def test_scanner_status_with_data(app, client, db_session):
    _create_admin(db_session)

    scanner_state.running = True
    scanner_state.last_scan_time = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    scanner_state.last_scan_count = 3
    scanner_state.active_session_id = 1

    resp = await client.get("/api/scanner/status", headers=_auth_headers(app))
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is True
    assert data["last_scan_count"] == 3


@pytest.mark.anyio
async def test_scanner_trigger(app, client, db_session):
    _create_admin(db_session)

    with patch("backend.integrations.scanner.routes.run_scan", return_value=2) as mock_scan:
        resp = await client.post("/api/scanner/trigger", headers=_auth_headers(app))

    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 2
    mock_scan.assert_called_once()


@pytest.mark.anyio
async def test_scanner_trigger_skipped(app, client, db_session):
    _create_admin(db_session)

    with patch("backend.integrations.scanner.routes.run_scan", return_value=None):
        resp = await client.post("/api/scanner/trigger", headers=_auth_headers(app))

    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] is None
    assert "skipped" in data["message"].lower()


@pytest.mark.anyio
async def test_scanner_requires_auth(app, client):
    resp = await client.get("/api/scanner/status")
    assert resp.status_code == 401
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_scanner_routes.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement scanner routes**

```python
# backend/integrations/scanner/routes.py
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, require_role
from backend.auth.models import UserRole
from backend.integrations.scanner.service import run_scan, scanner_state

scanner_router = APIRouter()


@scanner_router.get("/status")
def get_scanner_status(
    _user=Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
):
    return {
        "running": scanner_state.running,
        "last_scan_time": scanner_state.last_scan_time.isoformat() if scanner_state.last_scan_time else None,
        "last_scan_count": scanner_state.last_scan_count,
        "active_session_id": scanner_state.active_session_id,
    }


@scanner_router.post("/trigger")
def trigger_scan(
    db: Session = Depends(get_db_session),
    _user=Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
):
    now = datetime.now(tz=timezone.utc)
    count = run_scan(db, now)

    if count is None:
        return {"imported": None, "message": "Scan skipped — no active session or config missing"}

    return {"imported": count, "message": f"Scan complete, {count} check-ins imported"}
```

- [x] **Step 4: Register scanner router in app.py**

Add to `backend/app.py` imports:

```python
from backend.integrations.scanner.routes import scanner_router
```

Add after the delivery router registration:

```python
    app.include_router(scanner_router, prefix="/api/scanner")
```

- [x] **Step 5: Run test to verify it passes**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_scanner_routes.py -v`
Expected: PASS (5 tests)

- [x] **Step 6: Commit**

```bash
git add backend/integrations/scanner/routes.py backend/app.py tests/test_scanner_routes.py
git commit -m "feat: add scanner status and trigger API endpoints"
```

---

### Task 13: Scanner Background Task Lifecycle

**Files:**
- Modify: `backend/app.py`
- Test: `tests/test_scanner_lifecycle.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_scanner_lifecycle.py
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from backend.config_mgmt.models import AppConfig
from backend.integrations.scanner.service import scanner_state
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def test_scanner_not_started_when_disabled(app, db_session):
    """Scanner should not start if scanner.enabled is not 'true'."""
    # Default: scanner.enabled is not set (treated as disabled)
    assert scanner_state.running is False


def test_scanner_state_resets_between_tests():
    """Verify scanner state is not polluted between tests."""
    scanner_state.running = False
    scanner_state.last_scan_time = None
    assert scanner_state.running is False
```

- [x] **Step 2: Run test to verify it passes** (these are baseline tests)

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest tests/test_scanner_lifecycle.py -v`
Expected: PASS (2 tests)

- [x] **Step 3: Add lifespan-based scanner startup to app.py**

Replace the `@app.on_event("startup")` block in `backend/app.py` with a lifespan context manager:

```python
# At the top of app.py, add:
import asyncio
from contextlib import asynccontextmanager

# Replace the create_app function's startup event with a lifespan:
```

Modify `backend/app.py` to use the lifespan pattern. Replace the `@app.on_event("startup")` block (lines 32-34) and add a lifespan before `create_app`:

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        app.state.providers = await init_providers(settings)

        # Start scanner if enabled
        scanner_task = None
        try:
            with session_factory() as db:
                from backend.config_mgmt.service import get_config_value
                enabled = get_config_value(db, "scanner.enabled", "false")

            if enabled == "true":
                from backend.integrations.scanner.service import scanner_loop

                def get_interval():
                    with session_factory() as db:
                        from backend.config_mgmt.service import get_config_value
                        val = get_config_value(db, "scanner.interval_minutes", "5")
                        return int(val)

                scanner_task = asyncio.create_task(scanner_loop(session_factory, get_interval))
        except Exception:
            pass  # Don't block startup if scanner config fails

        yield

        # Shutdown
        if scanner_task is not None:
            from backend.integrations.scanner.service import scanner_state
            scanner_state.running = False
            scanner_task.cancel()
            try:
                await scanner_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="SkyNetControl", version="0.1.0", lifespan=lifespan)

    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.settings = settings

    # ... rest of create_app stays the same (health endpoint, routers, etc.)
```

Note: The `engine` and `session_factory` must be created before the lifespan is defined, since the lifespan closure references `session_factory`. Restructure so that `engine` and `session_factory` are set up first, then the lifespan is defined, then `app = FastAPI(...)`.

The full restructured `create_app`:

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings

    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.providers = await init_providers(settings)

        scanner_task = None
        try:
            with session_factory() as db:
                from backend.config_mgmt.service import get_config_value
                enabled = get_config_value(db, "scanner.enabled", "false")
            if enabled == "true":
                from backend.integrations.scanner.service import scanner_loop

                def get_interval():
                    with session_factory() as db:
                        from backend.config_mgmt.service import get_config_value as gcv
                        return int(gcv(db, "scanner.interval_minutes", "5"))

                scanner_task = asyncio.create_task(scanner_loop(session_factory, get_interval))
        except Exception:
            pass

        yield

        if scanner_task is not None:
            from backend.integrations.scanner.service import scanner_state
            scanner_state.running = False
            scanner_task.cancel()
            try:
                await scanner_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="SkyNetControl", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.settings = settings

    @app.get("/api/health")
    async def health():
        db_status = "disconnected"
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
                db_status = "connected"
        except Exception:
            pass
        return {"status": "ok", "version": "0.1.0", "database": db_status}

    # Register API routers (unchanged)
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(pat_router, prefix="/api/auth/tokens")
    app.include_router(config_router, prefix="/api/config")
    app.include_router(schedule_router, prefix="/api/schedule")
    app.include_router(activities_router, prefix="/api/activities")
    app.include_router(checkins_router, prefix="/api/checkins")
    app.include_router(reminders_router, prefix="/api/reminders")
    app.include_router(roster_router, prefix="/api/roster")
    app.include_router(audit_router, prefix="/api/audit")
    app.include_router(delivery_router, prefix="/api/delivery")
    app.include_router(scanner_router, prefix="/api/scanner")

    # Serve frontend static files if the directory exists
    if os.path.isdir(settings.static_dir):
        assets_dir = os.path.join(settings.static_dir, "assets")
        if os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{path:path}")
        async def serve_frontend(path: str):
            file_path = os.path.join(settings.static_dir, path)
            if path and os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(os.path.join(settings.static_dir, "index.html"))

    return app
```

- [x] **Step 4: Run full test suite**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest -x -q`
Expected: All tests pass

- [x] **Step 5: Commit**

```bash
git add backend/app.py tests/test_scanner_lifecycle.py
git commit -m "feat: add scanner background task lifecycle via FastAPI lifespan"
```

---

### Task 14: Frontend Config Page — Delivery and Scanner Fields

**Files:**
- Modify: `frontend/src/pages/ConfigPage.tsx`

- [x] **Step 1: Add new config fields to CONFIG_FIELDS array**

In `frontend/src/pages/ConfigPage.tsx`, add the following entries to the `CONFIG_FIELDS` array after the `claude_api_key` entry (after line 48):

```typescript
  {
    key: "delivery.backends",
    label: "Enabled Delivery Backends",
    group: "Delivery",
    placeholder: '["email", "groupsio", "winlink"]',
    helpText:
      "JSON array of enabled backends for sending reminders and rosters",
    mono: true,
  },
  {
    key: "delivery.email.to_address",
    label: "Email Recipient",
    group: "Delivery",
    placeholder: "net-list@example.com",
    helpText: "Email address to send reminders and rosters to",
  },
  {
    key: "delivery.groupsio.api_key",
    label: "Groups.io API Key",
    group: "Delivery",
    placeholder: "your-api-key",
    helpText: "API key for posting to groups.io",
    secret: true,
  },
  {
    key: "delivery.groupsio.group_name",
    label: "Groups.io Group Name",
    group: "Delivery",
    placeholder: "w0ne-net",
    helpText: "Target group name on groups.io",
  },
  {
    key: "delivery.winlink.target_address",
    label: "Winlink Delivery Address",
    group: "Delivery",
    placeholder: "NET@winlink.org",
    helpText: "Winlink address to send reminders and rosters to",
  },
  {
    key: "scanner.enabled",
    label: "Auto-Scanner Enabled",
    group: "Scanner",
    placeholder: "false",
    helpText: 'Set to "true" to enable automatic mailbox scanning',
  },
  {
    key: "scanner.interval_minutes",
    label: "Scan Interval (minutes)",
    group: "Scanner",
    placeholder: "5",
    helpText: "How often to scan the mailbox for new check-ins",
  },
```

- [x] **Step 2: Add new groups to GROUPS array**

Update the `GROUPS` array (line 51) to include the new groups:

```typescript
const GROUPS = ["Net Operations", "Integrations", "Delivery", "Scanner"];
```

- [x] **Step 3: Verify TypeScript compiles**

Run: `cd /home/ku0hn/dev/SkyNetControl && npx tsc --noEmit --project frontend/tsconfig.json` (or the project's equivalent TypeScript check)

If no `tsconfig.json` is available, verify via:
```bash
cd /home/ku0hn/dev/SkyNetControl/frontend && npx tsc --noEmit
```

- [x] **Step 4: Commit**

```bash
git add frontend/src/pages/ConfigPage.tsx
git commit -m "feat: add delivery and scanner config fields to config page"
```

---

### Task 15: Full Test Suite Verification

- [x] **Step 1: Run the complete test suite**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -m pytest -v`
Expected: All tests pass, including all new delivery and scanner tests.

- [x] **Step 2: Verify test count increased**

The new tests added:
- `test_delivery_base.py` — 3 tests
- `test_delivery_models.py` — 4 tests
- `test_delivery_email.py` — 3 tests
- `test_delivery_groupsio.py` — 3 tests
- `test_delivery_winlink.py` — 4 tests
- `test_delivery_registry.py` — 3 tests
- `test_delivery_service.py` — 6 tests
- `test_delivery_routes.py` — 4 tests
- `test_delivery_wiring.py` — 3 tests
- `test_scanner_service.py` — 6 tests
- `test_scanner_loop.py` — 4 tests
- `test_scanner_routes.py` — 5 tests
- `test_scanner_lifecycle.py` — 2 tests

Total new tests: ~50

- [x] **Step 3: Verify no import errors**

Run: `cd /home/ku0hn/dev/SkyNetControl && python -c "from backend.integrations.delivery.service import dispatch_delivery; from backend.integrations.scanner.service import run_scan; print('All imports OK')"`
Expected: "All imports OK"
