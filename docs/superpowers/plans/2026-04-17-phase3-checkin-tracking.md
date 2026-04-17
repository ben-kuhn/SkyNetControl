# Phase 3: Check-in Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add check-in tracking with PAT mailbox scanning, message parsing, timing classification, deduplication, manual entry, and long-term member roster tracking.

**Architecture:** Checkins module under `backend/modules/checkins/` following the existing model/service/routes pattern. A mailbox reader reads PAT message files from a configurable directory. A message parser detects form vs. plain text and extracts check-in fields. The check-in service orchestrates scanning, timing classification (on_time/early/late based on session window + grace period), and deduplication by callsign per session. A Member table tracks long-term participation and determines `is_new_member`.

**Tech Stack:** SQLAlchemy models, email.message (stdlib) for MIME parsing, FastAPI routes, Alembic migration

---

## File Structure

```
backend/
├── app.py                              # Modified: register checkins router
├── modules/
│   └── checkins/
│       ├── __init__.py
│       ├── models.py                   # RawMessage, CheckIn, Member
│       ├── mailbox_reader.py           # Read PAT message files from disk
│       ├── message_parser.py           # Detect type, extract check-in fields
│       ├── service.py                  # Scan, timing, dedup, member tracking
│       └── routes.py                   # /api/checkins/* endpoints
alembic/
├── env.py                              # Modified: add checkins model import
└── versions/
    └── 004_add_checkins_and_members.py # New migration
tests/
├── test_checkin_models.py
├── test_mailbox_reader.py
├── test_message_parser.py
├── test_checkin_service.py
└── test_checkin_routes.py
```

---

### Task 1: RawMessage, CheckIn, and Member Models

**Files:**
- Create: `backend/modules/checkins/__init__.py`
- Create: `backend/modules/checkins/models.py`
- Create: `tests/test_checkin_models.py`

- [ ] **Step 1: Create checkins package**

`backend/modules/checkins/__init__.py`:
```python
```

- [ ] **Step 2: Write the failing test**

`tests/test_checkin_models.py`:

```python
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.checkins.models import (
    RawMessage,
    MessageType,
    CheckIn,
    ParseStatus,
    TimingStatus,
    Member,
)
import backend.modules.schedule.models  # noqa: F401 — FK to net_sessions


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_raw_message(db: Session):
    msg = RawMessage(
        message_id="ABC123",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc),
        subject="Check-in",
        body="John Smith W0ABC Denver Denver CO Winlink",
        message_type=MessageType.PLAIN_TEXT,
    )
    db.add(msg)
    db.commit()

    fetched = db.get(RawMessage, msg.id)
    assert fetched is not None
    assert fetched.message_id == "ABC123"
    assert fetched.message_type == MessageType.PLAIN_TEXT
    assert fetched.parsed is False


def test_raw_message_id_is_unique(db: Session):
    msg1 = RawMessage(
        message_id="DUP1",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
        subject="First",
        body="body1",
        message_type=MessageType.PLAIN_TEXT,
    )
    msg2 = RawMessage(
        message_id="DUP1",
        from_address="W0XYZ@winlink.org",
        received_at=datetime(2026, 4, 10, 19, 0, tzinfo=timezone.utc),
        subject="Second",
        body="body2",
        message_type=MessageType.PLAIN_TEXT,
    )
    db.add(msg1)
    db.commit()
    db.add(msg2)
    with pytest.raises(Exception):
        db.commit()


def test_create_checkin(db: Session):
    # Need a session for FK — create a minimal season + session
    from backend.modules.schedule.models import (
        NetSeason,
        NetSession,
        SessionType,
    )
    from datetime import date

    season = NetSeason(
        name="Test Season",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
    )
    db.add(season)
    db.flush()

    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session)
    db.flush()

    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0ABC",
        name="John Smith",
        city="Denver",
        county="Denver",
        state="CO",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=True,
    )
    db.add(checkin)
    db.commit()

    fetched = db.get(CheckIn, checkin.id)
    assert fetched is not None
    assert fetched.callsign == "W0ABC"
    assert fetched.timing_status == TimingStatus.ON_TIME
    assert fetched.is_new_member is True
    assert fetched.latitude is None


def test_checkin_with_raw_message(db: Session):
    from backend.modules.schedule.models import (
        NetSeason,
        NetSession,
        SessionType,
    )
    from datetime import date

    season = NetSeason(
        name="Test Season",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
    )
    db.add(season)
    db.flush()

    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session)
    db.flush()

    raw = RawMessage(
        message_id="MSG001",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
        subject="Check-in",
        body="John Smith W0ABC Denver Denver CO Winlink",
        message_type=MessageType.PLAIN_TEXT,
    )
    db.add(raw)
    db.flush()

    checkin = CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="John Smith",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=False,
    )
    db.add(checkin)
    db.commit()

    fetched = db.get(CheckIn, checkin.id)
    assert fetched is not None
    assert fetched.raw_message_id == raw.id
    assert fetched.raw_message is not None
    assert fetched.raw_message.message_id == "MSG001"


def test_create_member(db: Session):
    member = Member(
        callsign="W0ABC",
        name="John Smith",
        first_check_in_date=datetime(2026, 1, 15, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 4, 10, tzinfo=timezone.utc),
        total_check_ins=12,
    )
    db.add(member)
    db.commit()

    fetched = db.get(Member, "W0ABC")
    assert fetched is not None
    assert fetched.name == "John Smith"
    assert fetched.total_check_ins == 12


def test_member_callsign_is_pk(db: Session):
    member1 = Member(
        callsign="W0ABC",
        name="John",
        first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        total_check_ins=1,
    )
    member2 = Member(
        callsign="W0ABC",
        name="John Updated",
        first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        total_check_ins=2,
    )
    db.add(member1)
    db.commit()
    db.add(member2)
    with pytest.raises(Exception):
        db.commit()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_checkin_models.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 4: Implement models**

`backend/modules/checkins/models.py`:

```python
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Float,
    Enum,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class MessageType(str, enum.Enum):
    FORM = "form"
    PLAIN_TEXT = "plain_text"
    UNKNOWN = "unknown"


class ParseStatus(str, enum.Enum):
    AUTO = "auto"
    MANUAL_REVIEW = "manual_review"
    MANUALLY_ENTERED = "manually_entered"


class TimingStatus(str, enum.Enum):
    ON_TIME = "on_time"
    EARLY = "early"
    LATE = "late"


class RawMessage(Base):
    __tablename__ = "raw_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[MessageType] = mapped_column(
        Enum(MessageType), nullable=False
    )
    parsed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    checkin: Mapped["CheckIn | None"] = relationship(back_populates="raw_message")


class CheckIn(Base):
    __tablename__ = "check_ins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("net_sessions.id"), nullable=False
    )
    raw_message_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("raw_messages.id"), nullable=True
    )
    callsign: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    county: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mode: Mapped[str] = mapped_column(String(100), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(ParseStatus), nullable=False
    )
    timing_status: Mapped[TimingStatus] = mapped_column(
        Enum(TimingStatus), nullable=False
    )
    is_new_member: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    raw_message: Mapped["RawMessage | None"] = relationship(back_populates="checkin")


class Member(Base):
    __tablename__ = "members"

    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    first_check_in_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_check_in_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    total_check_ins: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_checkin_models.py -v"`

Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add backend/modules/checkins/__init__.py backend/modules/checkins/models.py tests/test_checkin_models.py
git commit -m "feat: add RawMessage, CheckIn, and Member models"
```

---

### Task 2: PAT Mailbox Reader

**Files:**
- Create: `backend/modules/checkins/mailbox_reader.py`
- Create: `tests/test_mailbox_reader.py`

- [ ] **Step 1: Write the failing test**

`tests/test_mailbox_reader.py`:

```python
import os
import pytest
from datetime import datetime, timezone

from backend.modules.checkins.mailbox_reader import read_mailbox, read_message_file


@pytest.fixture
def mailbox_dir(tmp_path):
    """Create a temp directory with sample PAT-style message files."""
    # PAT stores messages as MIME-like files in the inbox.
    # Each file has headers (From, Subject, Date, Message-Id) and a body.
    msg1 = (
        "From: W0ABC@winlink.org\n"
        "Subject: Check-in\n"
        "Date: Thu, 10 Apr 2026 18:30:00 +0000\n"
        "Message-Id: <MSG001@winlink.org>\n"
        "To: w0ne@winlink.org\n"
        "\n"
        "John Smith W0ABC Denver Denver CO Winlink All good here\n"
    )
    msg2 = (
        "From: KD0TST@winlink.org\n"
        "Subject: Net Check-in Form\n"
        "Date: Thu, 10 Apr 2026 18:45:00 +0000\n"
        "Message-Id: <MSG002@winlink.org>\n"
        "To: w0ne@winlink.org\n"
        "\n"
        "Name: Jane Doe\n"
        "Callsign: KD0TST\n"
        "City: Boulder\n"
        "County: Boulder\n"
        "State: CO\n"
        "Mode: Winlink\n"
        "Comments: First time checking in!\n"
    )
    # A file that is NOT addressed to our net — should be filtered out
    msg3 = (
        "From: N0OTHER@winlink.org\n"
        "Subject: Hello\n"
        "Date: Thu, 10 Apr 2026 19:00:00 +0000\n"
        "Message-Id: <MSG003@winlink.org>\n"
        "To: someone.else@winlink.org\n"
        "\n"
        "This is a different conversation.\n"
    )

    (tmp_path / "MSG001.mime").write_text(msg1)
    (tmp_path / "MSG002.mime").write_text(msg2)
    (tmp_path / "MSG003.mime").write_text(msg3)
    (tmp_path / "not_a_message.txt").write_text("random file")
    return tmp_path


def test_read_single_message(mailbox_dir):
    result = read_message_file(mailbox_dir / "MSG001.mime")
    assert result is not None
    assert result["message_id"] == "<MSG001@winlink.org>"
    assert result["from_address"] == "W0ABC@winlink.org"
    assert result["subject"] == "Check-in"
    assert "John Smith" in result["body"]
    assert result["to_address"] == "w0ne@winlink.org"
    assert isinstance(result["received_at"], datetime)


def test_read_mailbox_filters_by_net_address(mailbox_dir):
    messages = read_mailbox(str(mailbox_dir), net_address="w0ne@winlink.org")
    # Should find MSG001 and MSG002 (addressed to w0ne), but not MSG003
    assert len(messages) == 2
    message_ids = {m["message_id"] for m in messages}
    assert "<MSG001@winlink.org>" in message_ids
    assert "<MSG002@winlink.org>" in message_ids
    assert "<MSG003@winlink.org>" not in message_ids


def test_read_mailbox_empty_dir(tmp_path):
    messages = read_mailbox(str(tmp_path), net_address="w0ne@winlink.org")
    assert messages == []


def test_read_mailbox_nonexistent_dir():
    messages = read_mailbox("/nonexistent/path", net_address="w0ne@winlink.org")
    assert messages == []


def test_read_message_file_malformed(tmp_path):
    bad_file = tmp_path / "bad.mime"
    bad_file.write_text("this is not a valid message")
    result = read_message_file(bad_file)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_mailbox_reader.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement mailbox reader**

`backend/modules/checkins/mailbox_reader.py`:

```python
import os
from datetime import datetime, timezone
from email import message_from_string, policy
from email.utils import parsedate_to_datetime
from pathlib import Path


def read_message_file(file_path: Path | str) -> dict | None:
    """Read a single MIME-format message file and return parsed headers + body.

    Returns None if the file cannot be parsed.
    """
    file_path = Path(file_path)
    try:
        text = file_path.read_text(errors="replace")
        msg = message_from_string(text, policy=policy.default)

        message_id = msg.get("Message-Id", "").strip()
        from_address = msg.get("From", "").strip()
        to_address = msg.get("To", "").strip()
        subject = msg.get("Subject", "").strip()
        date_str = msg.get("Date", "")

        if not message_id or not from_address:
            return None

        try:
            received_at = parsedate_to_datetime(date_str)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            received_at = datetime.now(timezone.utc)

        body = msg.get_body(preferencelist=("plain",))
        body_text = body.get_content().strip() if body else ""

        return {
            "message_id": message_id,
            "from_address": from_address,
            "to_address": to_address,
            "subject": subject,
            "received_at": received_at,
            "body": body_text,
        }
    except Exception:
        return None


def read_mailbox(
    mailbox_path: str,
    net_address: str,
) -> list[dict]:
    """Read all message files from a mailbox directory, filtered by net address.

    Reads all files with common message extensions (.mime, .b2f, .eml).
    Filters to only messages addressed to net_address (case-insensitive).
    """
    if not os.path.isdir(mailbox_path):
        return []

    net_addr_lower = net_address.lower()
    extensions = {".mime", ".b2f", ".eml"}
    messages = []

    for filename in os.listdir(mailbox_path):
        file_path = Path(mailbox_path) / filename
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in extensions:
            continue

        parsed = read_message_file(file_path)
        if parsed is None:
            continue

        # Filter by recipient address
        to_addr = parsed.get("to_address", "").lower()
        if net_addr_lower not in to_addr:
            continue

        messages.append(parsed)

    return messages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_mailbox_reader.py -v"`

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/checkins/mailbox_reader.py tests/test_mailbox_reader.py
git commit -m "feat: add PAT mailbox reader for message files"
```

---

### Task 3: Message Parser

**Files:**
- Create: `backend/modules/checkins/message_parser.py`
- Create: `tests/test_message_parser.py`

- [ ] **Step 1: Write the failing test**

`tests/test_message_parser.py`:

```python
import pytest

from backend.modules.checkins.message_parser import (
    detect_message_type,
    parse_form_message,
    parse_plain_text_message,
    parse_message,
)
from backend.modules.checkins.models import MessageType


def test_detect_form_message():
    body = (
        "Name: John Smith\n"
        "Callsign: W0ABC\n"
        "City: Denver\n"
        "County: Denver\n"
        "State: CO\n"
        "Mode: Winlink\n"
    )
    assert detect_message_type(body) == MessageType.FORM


def test_detect_plain_text_message():
    body = "John Smith W0ABC Denver Denver CO Winlink All good"
    assert detect_message_type(body) == MessageType.PLAIN_TEXT


def test_detect_unknown_message():
    body = "Hello, this is just a random email with no check-in data."
    assert detect_message_type(body) == MessageType.UNKNOWN


def test_parse_form_message():
    body = (
        "Name: John Smith\n"
        "Callsign: W0ABC\n"
        "City: Denver\n"
        "County: Denver\n"
        "State: CO\n"
        "Mode: Winlink\n"
        "Comments: All good here\n"
    )
    result = parse_form_message(body)
    assert result["name"] == "John Smith"
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["county"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Winlink"
    assert result["comments"] == "All good here"
    assert result["confidence"] == "high"


def test_parse_form_message_with_gps():
    body = (
        "Name: John Smith\n"
        "Callsign: W0ABC\n"
        "City: Denver\n"
        "State: CO\n"
        "Mode: Winlink\n"
        "Latitude: 39.7392\n"
        "Longitude: -104.9903\n"
    )
    result = parse_form_message(body)
    assert result["latitude"] == 39.7392
    assert result["longitude"] == -104.9903


def test_parse_form_message_missing_required():
    body = (
        "Name: John Smith\n"
        "City: Denver\n"
    )
    result = parse_form_message(body)
    assert result["confidence"] == "low"


def test_parse_plain_text_message():
    # Expected order: name, callsign, city, county, state, mode, comments
    body = "John Smith W0ABC Denver Denver CO Winlink All good here"
    result = parse_plain_text_message(body)
    assert result["name"] == "John Smith"
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Winlink"
    assert result["confidence"] == "medium"


def test_parse_plain_text_minimal():
    body = "John W0ABC Denver CO Winlink"
    result = parse_plain_text_message(body)
    assert result["callsign"] == "W0ABC"
    assert result["confidence"] == "medium"


def test_parse_plain_text_unparseable():
    body = "Hello"
    result = parse_plain_text_message(body)
    assert result["confidence"] == "low"


def test_parse_message_dispatches_form():
    body = (
        "Name: John Smith\n"
        "Callsign: W0ABC\n"
        "City: Denver\n"
        "State: CO\n"
        "Mode: Winlink\n"
    )
    msg_type, fields = parse_message(body)
    assert msg_type == MessageType.FORM
    assert fields["callsign"] == "W0ABC"


def test_parse_message_dispatches_plain_text():
    body = "John Smith W0ABC Denver Denver CO Winlink"
    msg_type, fields = parse_message(body)
    assert msg_type == MessageType.PLAIN_TEXT
    assert fields["callsign"] == "W0ABC"


def test_parse_message_unknown():
    body = "Random text with no check-in info"
    msg_type, fields = parse_message(body)
    assert msg_type == MessageType.UNKNOWN
    assert fields["confidence"] == "low"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_message_parser.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement message parser**

`backend/modules/checkins/message_parser.py`:

```python
import re

from backend.modules.checkins.models import MessageType

# Callsign pattern: 1-2 letters, digit, 1-3 letters (with optional suffix)
CALLSIGN_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z]{1,3}\b", re.IGNORECASE)

# Form fields we look for (case-insensitive)
FORM_FIELDS = {"name", "callsign", "city", "county", "state", "mode", "comments",
               "latitude", "longitude"}
REQUIRED_FORM_FIELDS = {"name", "callsign", "mode"}


def detect_message_type(body: str) -> MessageType:
    """Detect whether the message body is a structured form or plain text."""
    lines = body.strip().splitlines()
    field_count = 0
    for line in lines:
        if ":" in line:
            key = line.split(":", 1)[0].strip().lower()
            if key in FORM_FIELDS:
                field_count += 1

    if field_count >= 3:
        return MessageType.FORM

    # Check if it looks like a check-in (has a callsign)
    if CALLSIGN_RE.search(body):
        return MessageType.PLAIN_TEXT

    return MessageType.UNKNOWN


def parse_form_message(body: str) -> dict:
    """Parse a structured form message into check-in fields."""
    fields: dict = {}
    for line in body.strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key in FORM_FIELDS and value:
            fields[key] = value

    # Convert GPS coordinates if present
    latitude = None
    longitude = None
    if "latitude" in fields:
        try:
            latitude = float(fields.pop("latitude"))
        except ValueError:
            pass
    if "longitude" in fields:
        try:
            longitude = float(fields.pop("longitude"))
        except ValueError:
            pass

    # Uppercase callsign if present
    if "callsign" in fields:
        fields["callsign"] = fields["callsign"].upper()

    # Determine confidence based on required fields
    has_required = all(f in fields for f in REQUIRED_FORM_FIELDS)
    confidence = "high" if has_required else "low"

    return {
        "name": fields.get("name", ""),
        "callsign": fields.get("callsign", ""),
        "city": fields.get("city"),
        "county": fields.get("county"),
        "state": fields.get("state"),
        "mode": fields.get("mode", ""),
        "comments": fields.get("comments"),
        "latitude": latitude,
        "longitude": longitude,
        "confidence": confidence,
    }


def parse_plain_text_message(body: str) -> dict:
    """Parse a plain text check-in message.

    Expected order: name, callsign, city, county, state, mode, comments
    The callsign is used as the anchor point for parsing.
    """
    text = body.strip()

    # Find callsign — this is our anchor
    match = CALLSIGN_RE.search(text)
    if not match:
        return {
            "name": "",
            "callsign": "",
            "city": None,
            "county": None,
            "state": None,
            "mode": "",
            "comments": None,
            "latitude": None,
            "longitude": None,
            "confidence": "low",
        }

    callsign = match.group().upper()
    before_callsign = text[: match.start()].strip()
    after_callsign = text[match.end() :].strip()

    # Name is everything before the callsign
    name = before_callsign if before_callsign else ""

    # After callsign: city, county, state, mode, comments
    # Split by whitespace, try to assign fields
    parts = after_callsign.split() if after_callsign else []

    city = None
    county = None
    state = None
    mode = ""
    comments = None

    # Known modes for matching
    known_modes = {"winlink", "vara", "ardop", "packet", "pactor", "telnet", "ax.25"}

    # Find mode in parts (case-insensitive)
    mode_idx = None
    for i, part in enumerate(parts):
        if part.lower() in known_modes:
            mode_idx = i
            break

    if mode_idx is not None:
        mode = parts[mode_idx]
        location_parts = parts[:mode_idx]
        comment_parts = parts[mode_idx + 1 :]
        comments = " ".join(comment_parts) if comment_parts else None

        # Assign location: depends on count
        if len(location_parts) >= 3:
            city = location_parts[0]
            county = location_parts[1]
            state = location_parts[2]
        elif len(location_parts) == 2:
            city = location_parts[0]
            state = location_parts[1]
        elif len(location_parts) == 1:
            city = location_parts[0]
    else:
        # No mode found — assign what we can
        if len(parts) >= 1:
            city = parts[0]
        if len(parts) >= 2:
            state = parts[1]

    confidence = "medium" if callsign and name else "low"

    return {
        "name": name,
        "callsign": callsign,
        "city": city,
        "county": county,
        "state": state,
        "mode": mode,
        "comments": comments,
        "latitude": None,
        "longitude": None,
        "confidence": confidence,
    }


def parse_message(body: str) -> tuple[MessageType, dict]:
    """Detect message type and parse accordingly."""
    msg_type = detect_message_type(body)

    if msg_type == MessageType.FORM:
        return msg_type, parse_form_message(body)
    elif msg_type == MessageType.PLAIN_TEXT:
        return msg_type, parse_plain_text_message(body)
    else:
        return msg_type, {
            "name": "",
            "callsign": "",
            "city": None,
            "county": None,
            "state": None,
            "mode": "",
            "comments": None,
            "latitude": None,
            "longitude": None,
            "confidence": "low",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_message_parser.py -v"`

Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/checkins/message_parser.py tests/test_message_parser.py
git commit -m "feat: add message parser for form and plain text check-ins"
```

---

### Task 4: Check-in Service

**Files:**
- Create: `backend/modules/checkins/service.py`
- Create: `tests/test_checkin_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_checkin_service.py`:

```python
import pytest
from datetime import date, datetime, time, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)
from backend.modules.checkins.models import (
    RawMessage,
    MessageType,
    CheckIn,
    ParseStatus,
    TimingStatus,
    Member,
)
from backend.modules.checkins.service import (
    classify_timing,
    process_raw_message,
    scan_and_import_messages,
    get_checkins_for_session,
    create_manual_checkin,
    update_checkin,
    approve_session_checkins,
    is_new_member,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def season_and_session(db):
    season = NetSeason(
        name="Test Season",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 6, 30),
        day_of_week=3,  # Thursday
        time=time(18, 0),
    )
    db.add(season)
    db.flush()

    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session)
    db.commit()
    return season, net_session


def test_classify_timing_on_time(season_and_session):
    _, net_session = season_and_session
    received = datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc)
    assert classify_timing(net_session, received) == TimingStatus.ON_TIME


def test_classify_timing_early(season_and_session):
    _, net_session = season_and_session
    # Before session start but within grace period
    received = datetime(2026, 4, 9, 20, 0, tzinfo=timezone.utc)
    assert classify_timing(net_session, received) == TimingStatus.EARLY


def test_classify_timing_late(season_and_session):
    _, net_session = season_and_session
    # After session end but within grace period
    received = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
    assert classify_timing(net_session, received) == TimingStatus.LATE


def test_is_new_member(db):
    assert is_new_member(db, "W0NEW") is True

    member = Member(
        callsign="W0OLD",
        name="Old Timer",
        first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        total_check_ins=10,
    )
    db.add(member)
    db.commit()

    assert is_new_member(db, "W0OLD") is False


def test_process_raw_message_form(db, season_and_session):
    _, net_session = season_and_session
    raw = RawMessage(
        message_id="FORM001",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc),
        subject="Check-in",
        body=(
            "Name: John Smith\n"
            "Callsign: W0ABC\n"
            "City: Denver\n"
            "County: Denver\n"
            "State: CO\n"
            "Mode: Winlink\n"
            "Comments: All good\n"
        ),
        message_type=MessageType.FORM,
    )
    db.add(raw)
    db.commit()

    checkin = process_raw_message(db, raw, net_session)
    assert checkin is not None
    assert checkin.callsign == "W0ABC"
    assert checkin.name == "John Smith"
    assert checkin.city == "Denver"
    assert checkin.mode == "Winlink"
    assert checkin.parse_status == ParseStatus.AUTO
    assert checkin.timing_status == TimingStatus.ON_TIME
    assert raw.parsed is True


def test_process_raw_message_low_confidence(db, season_and_session):
    _, net_session = season_and_session
    raw = RawMessage(
        message_id="LOW001",
        from_address="W0XYZ@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc),
        subject="Hello",
        body="This is just a random email",
        message_type=MessageType.UNKNOWN,
    )
    db.add(raw)
    db.commit()

    checkin = process_raw_message(db, raw, net_session)
    assert checkin is not None
    assert checkin.parse_status == ParseStatus.MANUAL_REVIEW


def test_scan_and_import_deduplicates(db, season_and_session):
    _, net_session = season_and_session

    raw_messages = [
        {
            "message_id": "DUP_A",
            "from_address": "W0ABC@winlink.org",
            "to_address": "w0ne@winlink.org",
            "subject": "Check-in",
            "received_at": datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
            "body": "Name: John Smith\nCallsign: W0ABC\nCity: Denver\nState: CO\nMode: Winlink\n",
        },
        {
            "message_id": "DUP_B",
            "from_address": "W0ABC@winlink.org",
            "to_address": "w0ne@winlink.org",
            "subject": "Check-in corrected",
            "received_at": datetime(2026, 4, 10, 19, 0, tzinfo=timezone.utc),
            "body": "Name: John Smith\nCallsign: W0ABC\nCity: Aurora\nState: CO\nMode: Winlink\n",
        },
    ]
    checkins = scan_and_import_messages(db, raw_messages, net_session)

    # Should keep only the latest (DUP_B) for W0ABC
    assert len(checkins) == 1
    assert checkins[0].city == "Aurora"


def test_scan_and_import_skips_existing_message_ids(db, season_and_session):
    _, net_session = season_and_session

    # Pre-insert a raw message
    existing = RawMessage(
        message_id="EXISTING",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
        subject="Old",
        body="old",
        message_type=MessageType.PLAIN_TEXT,
        parsed=True,
    )
    db.add(existing)
    db.commit()

    raw_messages = [
        {
            "message_id": "EXISTING",
            "from_address": "W0ABC@winlink.org",
            "to_address": "w0ne@winlink.org",
            "subject": "Old",
            "received_at": datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
            "body": "old",
        },
    ]
    checkins = scan_and_import_messages(db, raw_messages, net_session)
    assert len(checkins) == 0


def test_get_checkins_for_session(db, season_and_session):
    _, net_session = season_and_session
    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0ABC",
        name="John",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    )
    db.add(checkin)
    db.commit()

    results = get_checkins_for_session(db, net_session.id)
    assert len(results) == 1
    assert results[0].callsign == "W0ABC"


def test_create_manual_checkin(db, season_and_session):
    _, net_session = season_and_session
    checkin = create_manual_checkin(
        db,
        session_id=net_session.id,
        callsign="W0MAN",
        name="Manual Entry",
        mode="Voice Relay",
        city="Pueblo",
        state="CO",
    )
    assert checkin.id is not None
    assert checkin.parse_status == ParseStatus.MANUALLY_ENTERED
    assert checkin.callsign == "W0MAN"


def test_update_checkin(db, season_and_session):
    _, net_session = season_and_session
    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0ABC",
        name="John",
        mode="Winlink",
        parse_status=ParseStatus.MANUAL_REVIEW,
        timing_status=TimingStatus.ON_TIME,
    )
    db.add(checkin)
    db.commit()

    updated = update_checkin(
        db, checkin.id, name="John Smith", city="Denver", parse_status=ParseStatus.AUTO
    )
    assert updated is not None
    assert updated.name == "John Smith"
    assert updated.city == "Denver"
    assert updated.parse_status == ParseStatus.AUTO


def test_approve_session_checkins(db, season_and_session):
    _, net_session = season_and_session
    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0NEW",
        name="New Person",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=True,
    )
    db.add(checkin)
    db.commit()

    approve_session_checkins(db, net_session.id)

    # Net session should be marked completed
    db.refresh(net_session)
    assert net_session.status == SessionStatus.COMPLETED

    # Member record should be created
    member = db.get(Member, "W0NEW")
    assert member is not None
    assert member.name == "New Person"
    assert member.total_check_ins == 1


def test_approve_updates_existing_member(db, season_and_session):
    _, net_session = season_and_session

    # Pre-existing member
    member = Member(
        callsign="W0OLD",
        name="Old Timer",
        first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        total_check_ins=10,
    )
    db.add(member)
    db.commit()

    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0OLD",
        name="Old Timer",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=False,
    )
    db.add(checkin)
    db.commit()

    approve_session_checkins(db, net_session.id)

    db.refresh(member)
    assert member.total_check_ins == 11
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_checkin_service.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement check-in service**

`backend/modules/checkins/service.py`:

```python
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from backend.modules.checkins.models import (
    CheckIn,
    Member,
    MessageType,
    ParseStatus,
    RawMessage,
    TimingStatus,
)
from backend.modules.checkins.message_parser import parse_message
from backend.modules.schedule.models import NetSession, SessionStatus


def classify_timing(
    net_session: NetSession, received_at: datetime
) -> TimingStatus:
    """Classify a message's timing relative to the session window + grace period."""
    # Session runs for the full day(s) of start_date to end_date
    session_start = datetime.combine(
        net_session.start_date, datetime.min.time(), tzinfo=timezone.utc
    )
    session_end = datetime.combine(
        net_session.end_date, datetime.max.time(), tzinfo=timezone.utc
    )

    grace = timedelta(hours=net_session.grace_period_hours)

    if session_start <= received_at <= session_end:
        return TimingStatus.ON_TIME
    elif session_start - grace <= received_at < session_start:
        return TimingStatus.EARLY
    elif session_end < received_at <= session_end + grace:
        return TimingStatus.LATE
    else:
        # Outside grace period — still accept but flag as early/late
        if received_at < session_start:
            return TimingStatus.EARLY
        return TimingStatus.LATE


def is_new_member(db: Session, callsign: str) -> bool:
    """Check if this callsign has never checked in before."""
    return db.get(Member, callsign) is None


def process_raw_message(
    db: Session, raw: RawMessage, net_session: NetSession
) -> CheckIn:
    """Parse a RawMessage and create a CheckIn record."""
    msg_type, fields = parse_message(raw.body)
    raw.message_type = msg_type
    raw.parsed = True

    callsign = fields.get("callsign", "").upper()
    confidence = fields.get("confidence", "low")

    # Determine parse status
    if confidence == "high":
        parse_status = ParseStatus.AUTO
    elif confidence == "medium":
        parse_status = ParseStatus.AUTO
    else:
        parse_status = ParseStatus.MANUAL_REVIEW

    # If we couldn't extract a callsign, try to get it from the from_address
    if not callsign and "@" in raw.from_address:
        callsign = raw.from_address.split("@")[0].upper()
        parse_status = ParseStatus.MANUAL_REVIEW

    timing = classify_timing(net_session, raw.received_at)
    new_member = is_new_member(db, callsign) if callsign else False

    checkin = CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign=callsign,
        name=fields.get("name", ""),
        city=fields.get("city"),
        county=fields.get("county"),
        state=fields.get("state"),
        mode=fields.get("mode", ""),
        comments=fields.get("comments"),
        latitude=fields.get("latitude"),
        longitude=fields.get("longitude"),
        parse_status=parse_status,
        timing_status=timing,
        is_new_member=new_member,
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)
    return checkin


def scan_and_import_messages(
    db: Session,
    raw_messages: list[dict],
    net_session: NetSession,
) -> list[CheckIn]:
    """Import raw message dicts, deduplicate by callsign (keep latest), skip existing."""
    # Filter out already-imported message IDs
    existing_ids = set()
    for msg in raw_messages:
        existing = (
            db.query(RawMessage)
            .filter(RawMessage.message_id == msg["message_id"])
            .first()
        )
        if existing:
            existing_ids.add(msg["message_id"])

    new_messages = [m for m in raw_messages if m["message_id"] not in existing_ids]

    if not new_messages:
        return []

    # Sort by received_at so latest messages come last
    new_messages.sort(key=lambda m: m["received_at"])

    # Import all as RawMessage and parse
    parsed_checkins: dict[str, CheckIn] = {}  # callsign -> CheckIn (last wins)
    for msg_dict in new_messages:
        msg_type, fields = parse_message(msg_dict["body"])
        raw = RawMessage(
            message_id=msg_dict["message_id"],
            from_address=msg_dict["from_address"],
            received_at=msg_dict["received_at"],
            subject=msg_dict["subject"],
            body=msg_dict["body"],
            message_type=msg_type,
            parsed=True,
        )
        db.add(raw)
        db.flush()

        checkin = process_raw_message(db, raw, net_session)
        if checkin.callsign:
            # Dedup: if we already have a check-in for this callsign, delete the old one
            if checkin.callsign in parsed_checkins:
                old = parsed_checkins[checkin.callsign]
                db.delete(old)
            parsed_checkins[checkin.callsign] = checkin

    db.commit()
    return list(parsed_checkins.values())


def get_checkins_for_session(
    db: Session, session_id: int
) -> list[CheckIn]:
    return (
        db.query(CheckIn)
        .filter(CheckIn.session_id == session_id)
        .order_by(CheckIn.callsign)
        .all()
    )


def create_manual_checkin(
    db: Session,
    session_id: int,
    callsign: str,
    name: str,
    mode: str,
    city: str | None = None,
    county: str | None = None,
    state: str | None = None,
    comments: str | None = None,
) -> CheckIn:
    new_member = is_new_member(db, callsign.upper())
    checkin = CheckIn(
        session_id=session_id,
        callsign=callsign.upper(),
        name=name,
        mode=mode,
        city=city,
        county=county,
        state=state,
        comments=comments,
        parse_status=ParseStatus.MANUALLY_ENTERED,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=new_member,
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)
    return checkin


def update_checkin(
    db: Session,
    checkin_id: int,
    name: str | None = None,
    callsign: str | None = None,
    city: str | None = None,
    county: str | None = None,
    state: str | None = None,
    mode: str | None = None,
    comments: str | None = None,
    parse_status: ParseStatus | None = None,
) -> CheckIn | None:
    checkin = db.get(CheckIn, checkin_id)
    if checkin is None:
        return None

    if name is not None:
        checkin.name = name
    if callsign is not None:
        checkin.callsign = callsign.upper()
    if city is not None:
        checkin.city = city
    if county is not None:
        checkin.county = county
    if state is not None:
        checkin.state = state
    if mode is not None:
        checkin.mode = mode
    if comments is not None:
        checkin.comments = comments
    if parse_status is not None:
        checkin.parse_status = parse_status

    db.commit()
    db.refresh(checkin)
    return checkin


def approve_session_checkins(db: Session, session_id: int) -> None:
    """Approve all check-ins for a session: update Member records, mark session completed."""
    checkins = get_checkins_for_session(db, session_id)
    now = datetime.now(timezone.utc)

    for checkin in checkins:
        if not checkin.callsign:
            continue

        member = db.get(Member, checkin.callsign)
        if member is None:
            member = Member(
                callsign=checkin.callsign,
                name=checkin.name,
                first_check_in_date=now,
                last_check_in_date=now,
                total_check_ins=1,
            )
            db.add(member)
        else:
            member.last_check_in_date = now
            member.total_check_ins += 1
            if checkin.name:
                member.name = checkin.name

    net_session = db.get(NetSession, session_id)
    if net_session is not None:
        net_session.status = SessionStatus.COMPLETED

    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_checkin_service.py -v"`

Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/checkins/service.py tests/test_checkin_service.py
git commit -m "feat: add check-in service with scan, timing, dedup, and approval"
```

---

### Task 5: Check-in API Routes

**Files:**
- Create: `backend/modules/checkins/routes.py`
- Create: `tests/test_checkin_routes.py`

- [ ] **Step 1: Write the failing test**

`tests/test_checkin_routes.py`:

```python
import os
import pytest
from datetime import date, datetime, timezone
from unittest.mock import patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config_mgmt.models import AppConfig
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)
from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
from backend.modules.checkins.routes import checkins_router
from backend.config import Settings


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
        "sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        nc = User(
            callsign="W0NC",
            oidc_subject="auth0|nc",
            name="Net Control",
            role=UserRole.NET_CONTROL,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, nc, viewer])

        # Config values for mailbox
        session.add(AppConfig(key="pat_mailbox_path", value="/tmp/test-mailbox"))
        session.add(AppConfig(key="net_address", value="w0ne@winlink.org"))

        # Season + session
        season = NetSeason(
            name="Test Season",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            day_of_week=3,
        )
        session.add(season)
        session.flush()

        net_session = NetSession(
            season_id=season.id,
            start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
        )
        session.add(net_session)
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
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_scan_mailbox(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)

    mock_messages = [
        {
            "message_id": "SCAN001",
            "from_address": "W0ABC@winlink.org",
            "to_address": "w0ne@winlink.org",
            "subject": "Check-in",
            "received_at": datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc),
            "body": "Name: John Smith\nCallsign: W0ABC\nCity: Denver\nState: CO\nMode: Winlink\n",
        },
    ]

    with patch("backend.modules.checkins.routes.read_mailbox") as mock_read:
        mock_read.return_value = mock_messages
        response = await test_client.post(
            "/api/checkins/scan/1",
            cookies={"access_token": token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 1
    assert len(data["checkins"]) == 1
    assert data["checkins"][0]["callsign"] == "W0ABC"


@pytest.mark.asyncio
async def test_get_checkins_for_session(test_client, test_settings, db_setup):
    # Seed a check-in directly
    with db_setup() as session:
        checkin = CheckIn(
            session_id=1,
            callsign="W0ABC",
            name="John Smith",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(checkin)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/checkins/session/1",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["callsign"] == "W0ABC"


@pytest.mark.asyncio
async def test_create_manual_checkin(test_client, test_settings):
    token = create_access_token("W0NC", "net_control", test_settings)
    response = await test_client.post(
        "/api/checkins/manual",
        json={
            "session_id": 1,
            "callsign": "W0MAN",
            "name": "Manual Entry",
            "mode": "Voice Relay",
            "city": "Pueblo",
            "state": "CO",
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["callsign"] == "W0MAN"
    assert data["parse_status"] == "manually_entered"


@pytest.mark.asyncio
async def test_update_checkin(test_client, test_settings, db_setup):
    with db_setup() as session:
        checkin = CheckIn(
            session_id=1,
            callsign="W0ABC",
            name="John",
            mode="Winlink",
            parse_status=ParseStatus.MANUAL_REVIEW,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(checkin)
        session.commit()
        checkin_id = checkin.id

    token = create_access_token("W0NC", "net_control", test_settings)
    response = await test_client.patch(
        f"/api/checkins/{checkin_id}",
        json={"name": "John Smith", "city": "Denver"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "John Smith"
    assert response.json()["city"] == "Denver"


@pytest.mark.asyncio
async def test_approve_session(test_client, test_settings, db_setup):
    with db_setup() as session:
        checkin = CheckIn(
            session_id=1,
            callsign="W0NEW",
            name="New Person",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
            is_new_member=True,
        )
        session.add(checkin)
        session.commit()

    token = create_access_token("W0NC", "net_control", test_settings)
    response = await test_client.post(
        "/api/checkins/approve/1",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["session_status"] == "completed"
    assert data["members_updated"] >= 1


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_scan(test_client, test_settings):
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)

    # Viewer can list check-ins
    response = await test_client.get(
        "/api/checkins/session/1",
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 200

    # Viewer cannot scan
    with patch("backend.modules.checkins.routes.read_mailbox") as mock_read:
        mock_read.return_value = []
        response = await test_client.post(
            "/api/checkins/scan/1",
            cookies={"access_token": viewer_token},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_members(test_client, test_settings, db_setup):
    from backend.modules.checkins.models import Member
    with db_setup() as session:
        member = Member(
            callsign="W0ABC",
            name="John Smith",
            first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_check_in_date=datetime(2026, 4, 10, tzinfo=timezone.utc),
            total_check_ins=12,
        )
        session.add(member)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/checkins/members",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["callsign"] == "W0ABC"
    assert data[0]["total_check_ins"] == 12
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_checkin_routes.py -v"`

Expected: FAIL — ImportError

- [ ] **Step 3: Implement check-in routes**

`backend/modules/checkins/routes.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.config_mgmt.service import get_config_value
from backend.modules.checkins.mailbox_reader import read_mailbox
from backend.modules.checkins.models import (
    CheckIn,
    Member,
    ParseStatus,
    TimingStatus,
)
from backend.modules.checkins.service import (
    approve_session_checkins,
    create_manual_checkin,
    get_checkins_for_session,
    scan_and_import_messages,
    update_checkin,
)
from backend.modules.schedule.models import NetSession

checkins_router = APIRouter(tags=["checkins"])


# --- Pydantic schemas ---


class ManualCheckinCreate(BaseModel):
    session_id: int
    callsign: str
    name: str
    mode: str
    city: str | None = None
    county: str | None = None
    state: str | None = None
    comments: str | None = None


class CheckinUpdate(BaseModel):
    name: str | None = None
    callsign: str | None = None
    city: str | None = None
    county: str | None = None
    state: str | None = None
    mode: str | None = None
    comments: str | None = None
    parse_status: ParseStatus | None = None


# --- Helpers ---


def _checkin_to_response(checkin: CheckIn) -> dict:
    return {
        "id": checkin.id,
        "session_id": checkin.session_id,
        "raw_message_id": checkin.raw_message_id,
        "callsign": checkin.callsign,
        "name": checkin.name,
        "city": checkin.city,
        "county": checkin.county,
        "state": checkin.state,
        "mode": checkin.mode,
        "comments": checkin.comments,
        "latitude": checkin.latitude,
        "longitude": checkin.longitude,
        "parse_status": checkin.parse_status.value,
        "timing_status": checkin.timing_status.value,
        "is_new_member": checkin.is_new_member,
    }


def _member_to_response(member: Member) -> dict:
    return {
        "callsign": member.callsign,
        "name": member.name,
        "first_check_in_date": member.first_check_in_date.isoformat(),
        "last_check_in_date": member.last_check_in_date.isoformat(),
        "total_check_ins": member.total_check_ins,
    }


# --- Routes ---


@checkins_router.post("/scan/{session_id}")
async def scan_mailbox_route(
    session_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    net_session = db.get(NetSession, session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    mailbox_path = get_config_value(db, "pat_mailbox_path")
    net_address = get_config_value(db, "net_address")
    if not mailbox_path or not net_address:
        raise HTTPException(
            status_code=503,
            detail="PAT mailbox path or net address not configured",
        )

    raw_messages = read_mailbox(mailbox_path, net_address=net_address)
    checkins = scan_and_import_messages(db, raw_messages, net_session)

    return {
        "imported": len(checkins),
        "checkins": [_checkin_to_response(c) for c in checkins],
    }


@checkins_router.get("/session/{session_id}")
async def get_session_checkins_route(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    checkins = get_checkins_for_session(db, session_id)
    return [_checkin_to_response(c) for c in checkins]


@checkins_router.post("/manual", status_code=201)
async def create_manual_checkin_route(
    body: ManualCheckinCreate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    net_session = db.get(NetSession, body.session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    checkin = create_manual_checkin(
        db,
        session_id=body.session_id,
        callsign=body.callsign,
        name=body.name,
        mode=body.mode,
        city=body.city,
        county=body.county,
        state=body.state,
        comments=body.comments,
    )
    return _checkin_to_response(checkin)


@checkins_router.patch("/{checkin_id}")
async def update_checkin_route(
    checkin_id: int,
    body: CheckinUpdate,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    checkin = update_checkin(
        db,
        checkin_id,
        name=body.name,
        callsign=body.callsign,
        city=body.city,
        county=body.county,
        state=body.state,
        mode=body.mode,
        comments=body.comments,
        parse_status=body.parse_status,
    )
    if checkin is None:
        raise HTTPException(status_code=404, detail="Check-in not found")
    return _checkin_to_response(checkin)


@checkins_router.post("/approve/{session_id}")
async def approve_session_route(
    session_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    net_session = db.get(NetSession, session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    checkins = get_checkins_for_session(db, session_id)
    approve_session_checkins(db, session_id)

    db.refresh(net_session)
    return {
        "session_status": net_session.status.value,
        "members_updated": len(checkins),
    }


@checkins_router.get("/members")
async def list_members_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    members = db.query(Member).order_by(Member.callsign).all()
    return [_member_to_response(m) for m in members]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_checkin_routes.py -v"`

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/modules/checkins/routes.py tests/test_checkin_routes.py
git commit -m "feat: add check-in API routes (scan, CRUD, approve, members)"
```

---

### Task 6: Alembic Migration

**Files:**
- Modify: `alembic/env.py`
- Auto-generate migration

- [ ] **Step 1: Add checkins model import to alembic/env.py**

Add after existing model imports in `alembic/env.py`:

```python
import backend.modules.checkins.models  # noqa: F401
```

- [ ] **Step 2: Auto-generate the migration**

Run: `nix-shell --run "alembic revision --autogenerate -m 'add checkins and members tables'"`

Expected: Creates a migration file with `raw_messages`, `check_ins`, and `members` tables

- [ ] **Step 3: Verify the migration has the correct tables**

Read the generated migration file. It should create 3 tables with correct columns and foreign keys.

- [ ] **Step 4: Run the migration**

Run: `nix-shell --run "alembic upgrade head"`

Expected: Migration applies successfully

- [ ] **Step 5: Clean up test database**

Run: `rm -f skynetcontrol.db`

- [ ] **Step 6: Commit**

```bash
git add alembic/env.py alembic/versions/
git commit -m "feat: add migration for checkins and members tables"
```

---

### Task 7: Wire Into app.py

**Files:**
- Modify: `backend/app.py`

- [ ] **Step 1: Register checkins router in app.py**

Add import at the top of `backend/app.py` after the existing router imports:

```python
from backend.modules.checkins.routes import checkins_router
```

Add router registration after the existing `include_router` calls:

```python
    app.include_router(checkins_router, prefix="/api/checkins")
```

- [ ] **Step 2: Run full test suite**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All tests pass (existing 83 + new checkin tests)

- [ ] **Step 3: Commit**

```bash
git add backend/app.py
git commit -m "feat: wire checkins module into app"
```

---

### Task 8: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All tests pass

- [ ] **Step 2: Verify Nix build**

Run: `nix-build default.nix`

Expected: Builds successfully (no new Nix dependencies for this phase — uses only stdlib)

- [ ] **Step 3: Clean up any test database files**

Run: `rm -f skynetcontrol.db`
