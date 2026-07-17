# Winlink Attachments + Inbound Form Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A bidirectional B2F attachment codec, inbound mailbox attachment extraction, reliable capture of received Winlink forms, and attachment/form metadata surfaced through the event message API.

**Architecture:** A pure `backend/integrations/winlink/b2f.py` codec (parse/build the FBB text-and-attachment layout). `WinlinkBackend.send` refactors onto it byte-identically. The mailbox reader routes `.b2f` through the codec (and `.mime`/`.eml` through stdlib email) to capture attachments, which persist in a new `raw_message_attachments` table. Received forms are captured from an attachment or the body via a `find_form_xml` helper; the event message API exposes an attachments summary, a download route, and a received-form metadata block.

**Tech Stack:** Python stdlib (`email`, dataclasses), SQLAlchemy 2.0, Alembic, pytest; React 19 + TS (a single indicator).

**Spec:** `docs/superpowers/specs/2026-07-16-winlink-attachments-design.md`.

## Global Constraints

- Host is NixOS: backend via `.venv/bin/...`; frontend via `cd frontend && nix-shell -p nodejs_22 --run "npm <…>"`.
- Lint: `nix-shell --run "ruff check"` — line-length 120, select E+F; production code has no per-file ignores.
- Commits: Conventional Commits (`feat(winlink): …`).
- Timestamps `datetime.now(timezone.utc)`, `DateTime(timezone=True)`.
- The codec handles the FBB text-and-attachment layout of already-decoded `.b2f` files — NOT the compressed B2 binary transfer protocol.
- `WinlinkBackend.send`'s current body-only output must stay BYTE-IDENTICAL after the refactor (existing `tests/test_delivery_winlink.py` is the guard).
- Attachment capture is best-effort and must NEVER block message import.
- Attachment download serves `Content-Type: application/octet-stream` with a sanitized `Content-Disposition` filename — never the claimed type, never rendered.
- Per-attachment and per-message size caps prevent storage exhaustion.
- Additive only to SP3's message payloads/routes — no changes to compose/send here.
- Do not push to remote; commit locally only.

## Interfaces this plan builds on (verified against the current tree)

- `backend/integrations/delivery/backends/winlink.py`: `WinlinkBackend.send(subject, body, config) -> DeliveryResult`; hand-builds a `.b2f` with headers `Mid/From/To/Subject/Mbo/Date/Body:<utf8-bytelen>` + blank line + body; `_strip_b2f_header_chars(value)` guard; writes `{mailbox}/out/{MID}.b2f`. Message id = `uuid.uuid4().hex[:12].upper()`; date = `now.strftime("%Y/%m/%d %H:%M")`.
- `backend/modules/checkins/mailbox_reader.py`: `read_message_file(path) -> dict | None` (currently parses ALL extensions via `email.message_from_string(policy=policy.default)`; returns `{path, message_id, from_address, to_address, subject, received_at, body}`). `read_mailbox(mailbox_path, net_address)` filters by `_to_matches_net`.
- `backend/modules/checkins/message_parser.py`: `extract_form_xml(body) -> str | None` (regex-slices `<RMS_Express_Form>...</RMS_Express_Form>`); `parse_winlink_form_message(body, known_modes=None) -> dict`; `extract_form_variables(root)`.
- `backend/modules/checkins/service.py`: `scan_and_import_messages(db, raw_messages, net_session, net_id=None)` — upserts `RawMessage` (deduped by message_id), `_upsert_source_paths` backfill pattern at the already-imported branch (~line 371) and the new-import branch (~line 393). Raw message dicts carry a `path` key.
- `backend/modules/checkins/models.py`: `RawMessage(id, message_id[unique], from_address, received_at, subject, body, message_type, parsed, source_path)` + `checkin` relationship.
- `backend/modules/events/routes.py`: `_message_to_response(m: EventMessage) -> dict` (no db handle); `list_messages_route` builds the messages list; `_get_event_or_404`, `_iso`, `require_net_role`, `NetRole`, `NetContext`, `get_db_session`, `Query`, `HTTPException` in scope.
- `backend/modules/events/models.py`: `EventMessage` has `raw_message_id` FK → raw_messages.id (nullable).
- Current alembic head: `c4d1e2f3a5b6`.
- Frontend: `MessagesPanel.tsx` row renders `from_callsign`/`subject`/`body`; `EventMessage` type in `types/index.ts`; `apiFetch` in `api/client.ts`.

---

### Task 1: B2F codec — parse & build

**Files:**
- Create: `backend/integrations/winlink/__init__.py` (empty)
- Create: `backend/integrations/winlink/b2f.py`
- Test: `tests/test_b2f_codec.py`

**Interfaces:**
- Produces (in `backend.integrations.winlink.b2f`): dataclasses `B2FAttachment(filename, content_type, data)`, `B2FMessage(headers, body, attachments)`; `B2FParseError`; `parse_b2f(raw: bytes) -> B2FMessage`; `build_b2f(*, message_id, from_addr, to_addr, subject, mbo, date, body, attachments=()) -> bytes`; `strip_b2f_header_chars(value: str) -> str`; `guess_content_type(filename: str) -> str`. Later tasks import these.

- [ ] **Step 1: Write failing codec tests**

```python
# tests/test_b2f_codec.py
import pytest

from backend.integrations.winlink.b2f import (
    B2FAttachment,
    B2FParseError,
    build_b2f,
    guess_content_type,
    parse_b2f,
    strip_b2f_header_chars,
)


def test_build_body_only_layout():
    out = build_b2f(
        message_id="ABC123", from_addr="W0NE", to_addr="KE0XYZ",
        subject="Hello", mbo="W0NE", date="2026/07/16 18:30", body="all clear",
    )
    text = out.decode("utf-8")
    assert text == (
        "Mid: ABC123\n"
        "From: W0NE\n"
        "To: KE0XYZ\n"
        "Subject: Hello\n"
        "Mbo: W0NE\n"
        "Date: 2026/07/16 18:30\n"
        "Body: 9\n"
        "\n"
        "all clear"
    )


def test_build_with_attachment():
    att = B2FAttachment(filename="RMS_Express_Form_ICS213.xml", content_type="application/xml", data=b"<x/>")
    out = build_b2f(
        message_id="M1", from_addr="W0NE", to_addr="KE0XYZ", subject="Form",
        mbo="W0NE", date="2026/07/16 18:30", body="see form", attachments=[att],
    )
    text = out.decode("utf-8")
    assert "Body: 8\n" in text
    assert "File: 4 RMS_Express_Form_ICS213.xml\n" in text
    assert text.endswith("<x/>")


def test_roundtrip_body_only():
    out = build_b2f(
        message_id="M1", from_addr="W0NE", to_addr="KE0XYZ", subject="Hi",
        mbo="W0NE", date="2026/07/16 18:30", body="body text",
    )
    msg = parse_b2f(out)
    assert msg.headers["Mid"] == "M1"
    assert msg.headers["From"] == "W0NE"
    assert msg.headers["Subject"] == "Hi"
    assert msg.body == "body text"
    assert msg.attachments == []


def test_roundtrip_with_attachments():
    atts = [
        B2FAttachment("form.xml", "application/xml", b"<RMS_Express_Form/>"),
        B2FAttachment("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0binary"),
    ]
    out = build_b2f(
        message_id="M1", from_addr="W0NE", to_addr="KE0XYZ", subject="Multi",
        mbo="W0NE", date="2026/07/16 18:30", body="two files", attachments=atts,
    )
    msg = parse_b2f(out)
    assert msg.body == "two files"
    assert len(msg.attachments) == 2
    assert msg.attachments[0].filename == "form.xml"
    assert msg.attachments[0].data == b"<RMS_Express_Form/>"
    assert msg.attachments[1].filename == "photo.jpg"
    assert msg.attachments[1].data == b"\xff\xd8\xff\xe0binary"


def test_parse_message_id_header_alias():
    raw = b"Message-Id: XYZ\nFrom: W0NE\nSubject: s\nBody: 2\n\nhi"
    msg = parse_b2f(raw)
    assert msg.headers.get("Mid", msg.headers.get("Message-Id")) == "XYZ"
    assert msg.body == "hi"


def test_parse_body_length_honored_not_newline():
    # Body length must be respected exactly — a body containing a newline that
    # would otherwise look like a File: section must not be mis-split.
    body = "line1\nFile: 3 fake\nline2"
    out = build_b2f(
        message_id="M1", from_addr="W0NE", to_addr="KE0XYZ", subject="s",
        mbo="W0NE", date="2026/07/16 18:30", body=body,
    )
    msg = parse_b2f(out)
    assert msg.body == body
    assert msg.attachments == []


class TestMalformed:
    def test_non_numeric_body_len(self):
        with pytest.raises(B2FParseError):
            parse_b2f(b"Mid: M1\nFrom: W0NE\nBody: notanumber\n\nhi")

    def test_file_len_exceeds_remaining(self):
        raw = b"Mid: M1\nFrom: W0NE\nBody: 2\n\nhiFile: 9999 big.xml\nshort"
        with pytest.raises(B2FParseError):
            parse_b2f(raw)

    def test_file_missing_filename(self):
        raw = b"Mid: M1\nFrom: W0NE\nBody: 2\n\nhiFile: 3\nabc"
        with pytest.raises(B2FParseError):
            parse_b2f(raw)

    def test_no_body_header(self):
        with pytest.raises(B2FParseError):
            parse_b2f(b"Mid: M1\nFrom: W0NE\nSubject: s\n\nno body header")


def test_strip_header_chars():
    assert strip_b2f_header_chars("a\r\nCc: evil") == "a Cc: evil"
    assert strip_b2f_header_chars("  trimmed  ") == "trimmed"


def test_guess_content_type():
    assert guess_content_type("form.xml") == "application/xml"
    assert guess_content_type("photo.jpg") == "image/jpeg"
    assert guess_content_type("unknown.zzz") == "application/octet-stream"
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_b2f_codec.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.integrations.winlink'`

- [ ] **Step 3: Implement the codec**

Create `backend/integrations/winlink/__init__.py` (empty), then:

```python
# backend/integrations/winlink/b2f.py
"""Bidirectional B2F/FBB codec: parse and build the text-and-attachment layout
of decoded .b2f files (as PAT and Winlink Express write them).

Layout:
    <Header: value>\n         (repeated)
    Body: <N>\n
    \n
    <N bytes of body>
    File: <M> <filename>\n     (repeated, each followed by M bytes)

NOT the compressed B2 binary transfer protocol — these are already-decoded
files on disk.
"""
import mimetypes
from dataclasses import dataclass, field


class B2FParseError(Exception):
    """Malformed .b2f content."""


@dataclass
class B2FAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class B2FMessage:
    headers: dict
    body: str
    attachments: list = field(default_factory=list)


_EXTRA_TYPES = {".b2f": "application/octet-stream", ".xml": "application/xml"}


def guess_content_type(filename: str) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _EXTRA_TYPES:
        return _EXTRA_TYPES[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def strip_b2f_header_chars(value: str) -> str:
    """Header-injection guard: the .b2f format is line-oriented, so CR/LF in a
    header value would split into extra headers. Replace with space and trim."""
    return value.replace("\r", " ").replace("\n", " ").strip()


def parse_b2f(raw: bytes) -> B2FMessage:
    """Parse decoded .b2f bytes into headers, body, and attachments."""
    # Split the header block from the payload at the first blank line.
    sep = raw.find(b"\n\n")
    if sep == -1:
        raise B2FParseError("no header/body separator")
    header_block = raw[:sep].decode("utf-8", errors="replace")
    payload = raw[sep + 2:]

    headers: dict = {}
    body_len: int | None = None
    for line in header_block.split("\n"):
        if not line.strip():
            continue
        if ":" not in line:
            raise B2FParseError(f"malformed header line: {line!r}")
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        headers[key] = val
        if key.lower() == "body":
            try:
                body_len = int(val)
            except ValueError:
                raise B2FParseError(f"non-numeric Body length: {val!r}")

    if body_len is None:
        raise B2FParseError("missing Body header")
    if body_len > len(payload):
        raise B2FParseError("Body length exceeds payload")

    body = payload[:body_len].decode("utf-8", errors="replace")
    rest = payload[body_len:]

    attachments: list = []
    while rest:
        # Skip a single separator newline between sections if present.
        if rest.startswith(b"\n"):
            rest = rest[1:]
        if not rest:
            break
        nl = rest.find(b"\n")
        if nl == -1:
            raise B2FParseError("attachment header not terminated")
        line = rest[:nl].decode("utf-8", errors="replace")
        if not line.startswith("File:"):
            raise B2FParseError(f"expected File: section, got {line!r}")
        spec = line[len("File:"):].strip()
        parts = spec.split(" ", 1)
        if len(parts) != 2 or not parts[1].strip():
            raise B2FParseError(f"malformed File: line: {line!r}")
        try:
            length = int(parts[0])
        except ValueError:
            raise B2FParseError(f"non-numeric File length: {parts[0]!r}")
        filename = parts[1].strip()
        data_start = nl + 1
        data_end = data_start + length
        if data_end > len(rest):
            raise B2FParseError("File length exceeds remaining bytes")
        data = rest[data_start:data_end]
        attachments.append(B2FAttachment(filename, guess_content_type(filename), data))
        rest = rest[data_end:]

    return B2FMessage(headers=headers, body=body, attachments=attachments)


def build_b2f(
    *,
    message_id: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    mbo: str,
    date: str,
    body: str,
    attachments=(),
) -> bytes:
    """Build a decoded .b2f file. Body-only output (no attachments) is
    byte-identical to the legacy WinlinkBackend format."""
    from_addr = strip_b2f_header_chars(from_addr)
    to_addr = strip_b2f_header_chars(to_addr)
    subject = strip_b2f_header_chars(subject)
    mbo = strip_b2f_header_chars(mbo)
    date = strip_b2f_header_chars(date)
    message_id = strip_b2f_header_chars(message_id)

    body_bytes = body.encode("utf-8")
    header = (
        f"Mid: {message_id}\n"
        f"From: {from_addr}\n"
        f"To: {to_addr}\n"
        f"Subject: {subject}\n"
        f"Mbo: {mbo}\n"
        f"Date: {date}\n"
        f"Body: {len(body_bytes)}\n"
        f"\n"
    )
    out = header.encode("utf-8") + body_bytes
    for att in attachments:
        out += f"\nFile: {len(att.data)} {att.filename}\n".encode("utf-8") + att.data
    return out
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_b2f_codec.py -q`
Expected: all pass. (Note the body-length round-trip test verifies a body containing a literal `File:` line is not mis-parsed — the length header, not newline-scanning, delimits the body.)

- [ ] **Step 5: Lint + commit**

```bash
nix-shell --run "ruff check"
git add backend/integrations/winlink/ tests/test_b2f_codec.py
git commit -m "feat(winlink): bidirectional B2F attachment codec"
```

---

### Task 2: Refactor WinlinkBackend onto the codec (byte-identical)

**Files:**
- Modify: `backend/integrations/delivery/backends/winlink.py`
- Test: `tests/test_delivery_winlink.py` (add a round-trip assertion; existing tests must pass unchanged)

**Interfaces:**
- Consumes: Task 1 `build_b2f`, `strip_b2f_header_chars`.
- Produces: `WinlinkBackend.send` unchanged signature/behavior; the emitted `.b2f` bytes are now produced by `build_b2f`. No new public surface.

- [ ] **Step 1: Add a byte-identity + round-trip test**

Add to `tests/test_delivery_winlink.py` (keep all existing tests):

```python
def test_send_output_roundtrips_through_codec(tmp_path):
    from backend.integrations.winlink.b2f import parse_b2f
    from backend.integrations.delivery.backends.winlink import WinlinkBackend

    backend = WinlinkBackend()
    config = {"mailbox_path": str(tmp_path), "target_address": "KE0XYZ", "callsign": "W0NE"}
    result = backend.send("Test Subject", "body content", config)
    assert result.success

    out_files = list((tmp_path / "out").glob("*.b2f"))
    assert len(out_files) == 1
    raw = out_files[0].read_bytes()
    msg = parse_b2f(raw)
    assert msg.headers["From"] == "W0NE"
    assert msg.headers["To"] == "KE0XYZ"
    assert msg.headers["Subject"] == "Test Subject"
    assert msg.body == "body content"
    assert msg.attachments == []
```

- [ ] **Step 2: Run it, verify it fails or passes appropriately**

Run: `.venv/bin/pytest tests/test_delivery_winlink.py -q`
Expected: the new test may already pass (the legacy format is parseable by the codec) — that's fine, it's the guard. Note the current pass/fail in the report.

- [ ] **Step 3: Refactor `send` onto `build_b2f`**

Replace the body of `WinlinkBackend.send` in `backend/integrations/delivery/backends/winlink.py`:

```python
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.integrations.delivery.backends.base import DeliveryResult
from backend.integrations.winlink.b2f import B2FAttachment, build_b2f


class WinlinkBackend:
    """Write a .b2f file to PAT's out/ directory for delivery on next sync."""

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        mailbox_path = config.get("mailbox_path", "")
        if not mailbox_path:
            return DeliveryResult(success=False, error="Winlink mailbox path not configured")

        target_address = config.get("target_address", "")
        callsign = config.get("callsign", "")
        # Optional attachments (used by forms composition in SP4b); a list of
        # B2FAttachment. Absent/empty for plain messages → byte-identical output.
        attachments = config.get("attachments") or ()

        try:
            out_dir = Path(mailbox_path) / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            message_id = uuid.uuid4().hex[:12].upper()
            now = datetime.now(tz=timezone.utc)
            date_str = now.strftime("%Y/%m/%d %H:%M")

            content = build_b2f(
                message_id=message_id,
                from_addr=callsign,
                to_addr=target_address,
                subject=subject,
                mbo=callsign,
                date=date_str,
                body=body,
                attachments=attachments,
            )

            filename = f"{message_id}.b2f"
            (out_dir / filename).write_bytes(content)

            return DeliveryResult(success=True, error=None)
        except Exception as exc:
            return DeliveryResult(success=False, error=str(exc))
```

Note: `_strip_b2f_header_chars` is now inside `build_b2f`, so the old module-level function is removed — confirm no other module imports it (grep `_strip_b2f_header_chars`); if something does, re-export `strip_b2f_header_chars` from the codec or leave a thin alias. The old code used `write_text`; `build_b2f` returns bytes so this is now `write_bytes` — the byte content for a body-only message is identical (UTF-8, `\n` line endings).

- [ ] **Step 4: Run the full delivery test file, verify pass**

Run: `.venv/bin/pytest tests/test_delivery_winlink.py -q`
Expected: all pass (existing + new round-trip). If any existing test asserts exact `.b2f` text, confirm it still matches byte-for-byte.

- [ ] **Step 5: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/integrations/delivery/backends/winlink.py tests/test_delivery_winlink.py
git commit -m "refactor(winlink): route WinlinkBackend through the B2F codec"
```

---

### Task 3: Attachments model + migration

**Files:**
- Modify: `backend/modules/checkins/models.py` (add `RawMessageAttachment`, relationship on `RawMessage`)
- Create: `alembic/versions/d5e2f6a1b3c7_add_raw_message_attachments.py`
- Test: `tests/test_raw_message_attachment_models.py`

**Interfaces:**
- Produces: `RawMessageAttachment(id, raw_message_id, filename, content_type, data, created_at)` with cascade delete from `RawMessage`; `RawMessage.attachments` relationship. Later tasks import `RawMessageAttachment`.

- [ ] **Step 1: Write failing model tests**

```python
# tests/test_raw_message_attachment_models.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.checkins.models import MessageType, RawMessage, RawMessageAttachment


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _raw(db):
    raw = RawMessage(
        message_id="M1", from_address="KE0XYZ", received_at=datetime.now(timezone.utc),
        subject="s", body="b", message_type=MessageType.UNKNOWN, parsed=False,
    )
    db.add(raw)
    db.commit()
    db.refresh(raw)
    return raw


def test_attachment_persists_binary(db):
    raw = _raw(db)
    att = RawMessageAttachment(
        raw_message_id=raw.id, filename="form.xml",
        content_type="application/xml", data=b"<x/>\xff",
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    assert att.data == b"<x/>\xff"
    assert att.created_at is not None


def test_attachments_relationship(db):
    raw = _raw(db)
    db.add(RawMessageAttachment(raw_message_id=raw.id, filename="a.xml", content_type="application/xml", data=b"a"))
    db.add(RawMessageAttachment(raw_message_id=raw.id, filename="b.jpg", content_type="image/jpeg", data=b"b"))
    db.commit()
    db.refresh(raw)
    assert len(raw.attachments) == 2


def test_cascade_delete_with_raw_message(db):
    raw = _raw(db)
    db.add(RawMessageAttachment(raw_message_id=raw.id, filename="a.xml", content_type="application/xml", data=b"a"))
    db.commit()
    db.delete(raw)
    db.commit()
    assert db.query(RawMessageAttachment).count() == 0
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_raw_message_attachment_models.py -q`
Expected: FAIL — `ImportError` (`RawMessageAttachment` not defined)

- [ ] **Step 3: Add the model**

In `backend/modules/checkins/models.py`, ensure `LargeBinary` is imported from sqlalchemy, add `_utcnow`-style default (match the file's existing timestamp style — if the file uses `datetime.now(timezone.utc)` inline defaults, mirror that), then append:

```python
class RawMessageAttachment(Base):
    __tablename__ = "raw_message_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_messages.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
```

And add to `class RawMessage` (next to the `checkin` relationship):

```python
    attachments: Mapped[list["RawMessageAttachment"]] = relationship(
        cascade="all, delete-orphan"
    )
```

(Confirm `ForeignKey`, `String`, `Integer`, `DateTime`, `LargeBinary`, `Mapped`, `mapped_column`, `relationship` are all imported at the top of the file; add `LargeBinary` if missing.)

- [ ] **Step 4: Run model tests, verify pass**

Run: `.venv/bin/pytest tests/test_raw_message_attachment_models.py -q`
Expected: 3 passed

- [ ] **Step 5: Write the migration**

```python
# alembic/versions/d5e2f6a1b3c7_add_raw_message_attachments.py
"""add raw message attachments

Revision ID: d5e2f6a1b3c7
Revises: c4d1e2f3a5b6
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e2f6a1b3c7'
down_revision: Union[str, None] = 'c4d1e2f3a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'raw_message_attachments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('raw_message_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['raw_message_id'], ['raw_messages.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('raw_message_attachments')
```

- [ ] **Step 6: Verify migration on a scratch DB**

Run:
```bash
SKYNET_DATABASE_URL="sqlite:////tmp/claude-att-mig.db" .venv/bin/alembic upgrade head && rm -f /tmp/claude-att-mig.db
```
Expected: ends with `Running upgrade c4d1e2f3a5b6 -> d5e2f6a1b3c7, add raw message attachments`, exit 0.

- [ ] **Step 7: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/checkins/models.py alembic/versions/d5e2f6a1b3c7_add_raw_message_attachments.py tests/test_raw_message_attachment_models.py
git commit -m "feat(checkins): raw_message_attachments table and migration"
```

---

### Task 4: Mailbox reader captures attachments + import persists them

**Files:**
- Modify: `backend/modules/checkins/mailbox_reader.py` (`read_message_file`)
- Modify: `backend/modules/checkins/service.py` (`scan_and_import_messages` + a persist helper)
- Test: `tests/test_mailbox_reader.py` (add), `tests/test_checkin_attachment_import.py` (create)

**Interfaces:**
- Consumes: Task 1 codec (`parse_b2f`, `B2FParseError`, `guess_content_type`); Task 3 `RawMessageAttachment`.
- Produces: `read_message_file` return dict gains `"attachments": [{filename, content_type, data}]` (empty list when none); module constants `MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024`, `MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024`. `scan_and_import_messages` persists attachments for new imports and backfills on already-imported rows that have none.

- [ ] **Step 1: Write failing reader tests**

```python
# add to tests/test_mailbox_reader.py
from pathlib import Path

from backend.modules.checkins.mailbox_reader import read_message_file


def _write(tmp_path, name, content: bytes) -> Path:
    p = Path(tmp_path) / name
    p.write_bytes(content)
    return p


def test_b2f_attachment_extracted(tmp_path):
    from backend.integrations.winlink.b2f import B2FAttachment, build_b2f

    att = B2FAttachment("RMS_Express_Form_ICS213.xml", "application/xml", b"<RMS_Express_Form/>")
    raw = build_b2f(
        message_id="M1", from_addr="KE0XYZ@winlink.org", to_addr="W0NE",
        subject="ICS213", mbo="KE0XYZ", date="2026/07/16 18:30",
        body="see attached form", attachments=[att],
    )
    p = _write(tmp_path, "M1.b2f", raw)
    parsed = read_message_file(p)
    assert parsed["body"] == "see attached form"
    assert len(parsed["attachments"]) == 1
    assert parsed["attachments"][0]["filename"] == "RMS_Express_Form_ICS213.xml"
    assert parsed["attachments"][0]["data"] == b"<RMS_Express_Form/>"


def test_b2f_body_only_has_empty_attachments(tmp_path):
    from backend.integrations.winlink.b2f import build_b2f

    raw = build_b2f(
        message_id="M2", from_addr="KE0XYZ", to_addr="W0NE", subject="s",
        mbo="KE0XYZ", date="2026/07/16 18:30", body="plain",
    )
    p = _write(tmp_path, "M2.b2f", raw)
    parsed = read_message_file(p)
    assert parsed["body"] == "plain"
    assert parsed["attachments"] == []


def test_malformed_b2f_falls_back_to_body_only(tmp_path):
    # A .b2f whose File section is corrupt must still yield the message (body)
    # via the email fallback, never None, with empty attachments.
    raw = b"Mid: M3\nFrom: KE0XYZ\nTo: W0NE\nSubject: s\nBody: 5\n\nhelloFile: 999 x\nshort"
    p = _write(tmp_path, "M3.b2f", raw)
    parsed = read_message_file(p)
    assert parsed is not None
    assert parsed["attachments"] == []
    # body recovered via the email fallback (may include the trailing text)
    assert "hello" in parsed["body"]
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest tests/test_mailbox_reader.py -q`
Expected: FAIL — `KeyError: 'attachments'` on the new tests

- [ ] **Step 3: Modify `read_message_file`**

In `backend/modules/checkins/mailbox_reader.py`, add near the top:

```python
from backend.integrations.winlink.b2f import B2FParseError, guess_content_type, parse_b2f

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
```

Restructure `read_message_file` so `.b2f` files parse via the codec first (which gives clean body + attachments), and `.mime`/`.eml` continue through `email` (with an attachment walk). Concretely, after computing `file_path`, branch:

```python
def read_message_file(file_path: Path | str) -> dict | None:
    file_path = Path(file_path)
    if file_path.suffix.lower() == ".b2f":
        parsed = _read_b2f(file_path)
        if parsed is not None:
            return parsed
        # Fall through to the generic email parser on codec failure.
    return _read_email(file_path)
```

Add `_read_b2f`:

```python
def _read_b2f(file_path: Path) -> dict | None:
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        logger.warning("Cannot read mailbox message %s: %s", file_path, exc)
        return None
    try:
        msg = parse_b2f(raw)
    except B2FParseError:
        logger.debug("Malformed .b2f, falling back to email parser: %s", file_path)
        return None

    headers = msg.headers
    message_id = (headers.get("Message-Id") or headers.get("Mid") or "").strip()
    from_address = (headers.get("From") or "").strip()
    if not message_id or not from_address:
        return None
    received_at = _parse_date(headers.get("Date", ""))
    attachments = _cap_attachments(
        [{"filename": a.filename, "content_type": a.content_type, "data": a.data} for a in msg.attachments]
    )
    return {
        "path": str(file_path),
        "message_id": message_id,
        "from_address": from_address,
        "to_address": (headers.get("To") or "").strip(),
        "subject": (headers.get("Subject") or "").strip(),
        "received_at": received_at,
        "body": msg.body.strip(),
        "attachments": attachments,
    }
```

Rename the existing parse body into `_read_email(file_path)` (the current logic verbatim) but: return the same dict PLUS an `"attachments"` key built from the email non-text parts:

```python
def _read_email(file_path: Path) -> dict | None:
    # ... existing read_text + message_from_string logic unchanged, up to body_text ...
    attachments = _cap_attachments(_email_attachments(msg))
    return {
        # ... existing keys ...
        "attachments": attachments,
    }


def _email_attachments(msg) -> list[dict]:
    out = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = part.get_content_disposition()
        filename = part.get_filename()
        if disp == "attachment" or (filename and part.get_content_maintype() != "text"):
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            out.append({
                "filename": filename or "attachment",
                "content_type": part.get_content_type() or guess_content_type(filename or ""),
                "data": payload,
            })
    return out
```

Factor the existing date logic into `_parse_date(date_str) -> datetime` (reuse in both), and add the caps helper:

```python
def _cap_attachments(attachments: list[dict]) -> list[dict]:
    kept: list[dict] = []
    total = 0
    for att in attachments:
        size = len(att["data"])
        if size > MAX_ATTACHMENT_BYTES:
            logger.warning("Skipping oversized attachment %s (%d bytes)", att["filename"], size)
            continue
        if total + size > MAX_TOTAL_ATTACHMENT_BYTES:
            logger.warning("Attachment total cap reached; skipping %s", att["filename"])
            continue
        total += size
        kept.append(att)
    return kept
```

Ensure existing (non-.b2f) callers still get a `body`/`attachments` dict. Existing mailbox-reader tests must still pass.

- [ ] **Step 4: Run reader tests, verify pass**

Run: `.venv/bin/pytest tests/test_mailbox_reader.py -q`
Expected: all pass (new + existing).

- [ ] **Step 5: Write failing import-persistence tests**

```python
# tests/test_checkin_attachment_import.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.checkins.models import RawMessage, RawMessageAttachment
from backend.modules.checkins.service import scan_and_import_messages
from backend.modules.schedule.models import NetSession, SessionType, SessionStatus
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _stub_geocode(monkeypatch):
    from backend.integrations.geocoder import service as geo
    monkeypatch.setattr(geo, "_call_nominatim", lambda *a, **kw: None)


def _session(db, net):
    s = NetSession(
        net_id=net.id, start_date=datetime.now(timezone.utc).date(),
        session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
        grace_period_hours=24,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _msg(mid="M1", atts=None):
    return {
        "message_id": mid, "from_address": "KE0XYZ", "to_address": "W0NE",
        "subject": "s", "body": "KE0XYZ Ben Kuhn", "received_at": datetime.now(timezone.utc),
        "path": None, "attachments": atts or [],
    }


def test_attachments_persisted_on_import(db):
    net = make_test_net(db)
    session = _session(db, net)
    att = {"filename": "form.xml", "content_type": "application/xml", "data": b"<x/>"}
    scan_and_import_messages(db, [_msg(atts=[att])], session, net_id=net.id)
    raw = db.query(RawMessage).filter_by(message_id="M1").one()
    assert len(raw.attachments) == 1
    assert raw.attachments[0].filename == "form.xml"
    assert raw.attachments[0].data == b"<x/>"


def test_attachments_backfilled_on_rescan(db):
    net = make_test_net(db)
    session = _session(db, net)
    # First import with no attachments (simulating a pre-feature import).
    scan_and_import_messages(db, [_msg(atts=[])], session, net_id=net.id)
    assert db.query(RawMessageAttachment).count() == 0
    # Re-scan with attachments present → backfilled.
    att = {"filename": "form.xml", "content_type": "application/xml", "data": b"<x/>"}
    scan_and_import_messages(db, [_msg(atts=[att])], session, net_id=net.id)
    assert db.query(RawMessageAttachment).count() == 1
```

- [ ] **Step 6: Run, verify failure**

Run: `.venv/bin/pytest tests/test_checkin_attachment_import.py -q`
Expected: FAIL — attachments not persisted

- [ ] **Step 7: Persist attachments in `scan_and_import_messages`**

In `backend/modules/checkins/service.py`, add a helper and call it in both branches. Add:

```python
def _persist_attachments(db, raw, attachment_dicts) -> None:
    """Best-effort attachment persistence — never blocks message import."""
    from backend.modules.checkins.models import RawMessageAttachment

    if not attachment_dicts:
        return
    try:
        for att in attachment_dicts:
            db.add(RawMessageAttachment(
                raw_message_id=raw.id,
                filename=att["filename"][:255],
                content_type=att["content_type"][:255],
                data=att["data"],
            ))
        db.flush()
    except Exception:
        logger.warning("Failed to persist attachments for message %s", raw.message_id, exc_info=True)
        db.rollback()


def _backfill_attachments(db, message_dicts) -> None:
    """For already-imported messages with no attachment rows yet, add them."""
    from backend.modules.checkins.models import RawMessage, RawMessageAttachment

    by_id = {m["message_id"]: m for m in message_dicts if m.get("attachments")}
    if not by_id:
        return
    rows = db.query(RawMessage).filter(RawMessage.message_id.in_(by_id.keys())).all()
    for raw in rows:
        existing = db.query(RawMessageAttachment).filter_by(raw_message_id=raw.id).count()
        if existing == 0:
            _persist_attachments(db, raw, by_id[raw.message_id]["attachments"])
```

- In the new-import loop, right after the `RawMessage` row is created + flushed (where `raw` exists), call `_persist_attachments(db, raw, msg_dict.get("attachments") or [])`.
- In the already-imported handling (both the early-return branch ~line 371 and the mixed branch), call `_backfill_attachments(db, already_imported)` alongside the existing `_upsert_source_paths(db, already_imported)`.

Read the function fully before editing; preserve the callsign-dedup, source_path backfill, and CHECKINS_READY notification. Attachment persistence is additive and best-effort.

- [ ] **Step 8: Run persistence tests + regression, verify pass**

Run: `.venv/bin/pytest tests/test_checkin_attachment_import.py tests/test_checkin_service.py tests/test_scanner_event_routing.py -q`
Expected: all pass.

- [ ] **Step 9: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/checkins/mailbox_reader.py backend/modules/checkins/service.py tests/test_mailbox_reader.py tests/test_checkin_attachment_import.py
git commit -m "feat(checkins): capture and persist inbound message attachments"
```

---

### Task 5: Received-form capture + attachment/form message API

**Files:**
- Modify: `backend/modules/checkins/message_parser.py` (add `find_form_xml`)
- Modify: `backend/modules/events/routes.py` (attachments summary + form block on message response; download route)
- Test: `tests/test_find_form_xml.py` (create), `tests/test_event_message_attachments_api.py` (create)

**Interfaces:**
- Consumes: Task 3 `RawMessageAttachment`; Task 1 codec not needed here; existing `extract_form_xml`.
- Produces: `find_form_xml(attachments, body) -> str | None` in message_parser; message response gains `attachments: [{id, filename, content_type, size}]` and `form: {display_form, reply_template, is_form} | None`; route `GET /{event_id}/messages/{message_id}/attachments/{attachment_id}`.

- [ ] **Step 1: Write failing find_form_xml tests**

```python
# tests/test_find_form_xml.py
from backend.modules.checkins.message_parser import find_form_xml

FORM_XML = "<RMS_Express_Form><form_parameters><display_form>ICS213.html</display_form>" \
           "<reply_template>ICS213Reply.txt</reply_template></form_parameters>" \
           "<variables><msgbody>hi</msgbody></variables></RMS_Express_Form>"


def test_finds_form_in_attachment():
    atts = [
        {"filename": "photo.jpg", "content_type": "image/jpeg", "data": b"\xff\xd8"},
        {"filename": "RMS_Express_Form_ICS213.xml", "content_type": "application/xml",
         "data": FORM_XML.encode("utf-8")},
    ]
    assert find_form_xml(atts, body="unrelated body") == FORM_XML


def test_finds_form_in_body_fallback():
    assert find_form_xml([], body=f"prose\n{FORM_XML}\nfooter") == FORM_XML


def test_attachment_wins_over_body():
    body_form = FORM_XML.replace("ICS213.html", "BODY.html")
    atts = [{"filename": "RMS_Express_Form_ICS213.xml", "content_type": "application/xml",
             "data": FORM_XML.encode("utf-8")}]
    assert "ICS213.html" in find_form_xml(atts, body=body_form)


def test_none_when_no_form():
    assert find_form_xml([], body="just text") is None
    assert find_form_xml([{"filename": "x.jpg", "content_type": "image/jpeg", "data": b"x"}], body="") is None
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest tests/test_find_form_xml.py -q`
Expected: FAIL — `find_form_xml` not defined

- [ ] **Step 3: Implement `find_form_xml`**

In `backend/modules/checkins/message_parser.py`, after `extract_form_xml`:

```python
import fnmatch


def find_form_xml(attachments, body: str) -> str | None:
    """Return the <RMS_Express_Form> XML from an RMS_Express_Form_*.xml
    attachment (preferred — how real Winlink Express sends forms) or from the
    message body (fallback — how some paths inline it).

    `attachments` is a list of dicts with keys filename/content_type/data.
    """
    for att in attachments or []:
        name = att.get("filename", "")
        if fnmatch.fnmatch(name.lower(), "rms_express_form_*.xml"):
            try:
                text = att["data"].decode("utf-8", errors="replace")
            except (AttributeError, UnicodeDecodeError):
                continue
            found = extract_form_xml(text)
            if found:
                return found
    return extract_form_xml(body or "")
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest tests/test_find_form_xml.py -q`
Expected: 4 passed

- [ ] **Step 5: Write failing API tests**

```python
# tests/test_event_message_attachments_api.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.checkins.models import MessageType, RawMessage, RawMessageAttachment
from backend.modules.events.models import EventMessage, MessageDirection, MessageStatus
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"

FORM_XML = ("<RMS_Express_Form><form_parameters><display_form>ICS213.html</display_form>"
            "<reply_template>ICS213Reply.txt</reply_template></form_parameters>"
            "<variables><msgbody>hi</msgbody></variables></RMS_Express_Form>")


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        from datetime import datetime, timezone
        from backend.modules.events.models import Event, EventType, EventStatus

        nc = User(callsign="W0NC", oidc_subject="auth0|nc", name="NC")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        session.add_all([nc, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        event = Event(net_id=net.id, name="E", event_type=EventType.EMERGENCY,
                      created_by="W0NC", status=EventStatus.ACTIVE)
        session.add(event)
        session.flush()
        raw = RawMessage(message_id="M1", from_address="KE0XYZ", received_at=datetime.now(timezone.utc),
                         subject="ICS213", body="see form", message_type=MessageType.WINLINK_FORM, parsed=True)
        session.add(raw)
        session.flush()
        att = RawMessageAttachment(raw_message_id=raw.id, filename="RMS_Express_Form_ICS213.xml",
                                   content_type="application/xml", data=FORM_XML.encode("utf-8"))
        session.add(att)
        msg = EventMessage(event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
                           raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE",
                           subject="ICS213", body="see form", status=MessageStatus.UNREAD)
        session.add(msg)
        session.commit()
        yield {"engine": engine, "factory": factory, "event_id": event.id,
               "message_id": msg.id, "attachment_id": att.id}
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app
    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    return application


@pytest.fixture
async def nc_client(app, test_settings):
    token = make_test_token("W0NC", test_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


class TestMessagePayload:
    async def test_attachments_summary_and_form_block(self, nc_client, db_setup):
        resp = await nc_client.get(f"{BASE}/{db_setup['event_id']}/messages")
        assert resp.status_code == 200
        msg = resp.json()["messages"][0]
        assert len(msg["attachments"]) == 1
        att = msg["attachments"][0]
        assert att["filename"] == "RMS_Express_Form_ICS213.xml"
        assert att["size"] == len(FORM_XML.encode("utf-8"))
        assert "data" not in att  # bytes never inline
        assert msg["form"]["is_form"] is True
        assert msg["form"]["display_form"] == "ICS213.html"
        assert msg["form"]["reply_template"] == "ICS213Reply.txt"


class TestDownload:
    async def test_download_streams_bytes(self, nc_client, db_setup):
        resp = await nc_client.get(
            f"{BASE}/{db_setup['event_id']}/messages/{db_setup['message_id']}"
            f"/attachments/{db_setup['attachment_id']}"
        )
        assert resp.status_code == 200
        assert resp.content == FORM_XML.encode("utf-8")
        assert resp.headers["content-type"] == "application/octet-stream"
        assert "RMS_Express_Form_ICS213.xml" in resp.headers.get("content-disposition", "")

    async def test_download_missing_404(self, nc_client, db_setup):
        resp = await nc_client.get(
            f"{BASE}/{db_setup['event_id']}/messages/{db_setup['message_id']}/attachments/9999"
        )
        assert resp.status_code == 404
```

- [ ] **Step 6: Run, verify failure**

Run: `.venv/bin/pytest tests/test_event_message_attachments_api.py -q`
Expected: FAIL — `attachments`/`form` keys missing; download route 404/405

- [ ] **Step 7: Wire the message payload + download route**

In `backend/modules/events/routes.py`:

Add a helper that builds the per-message attachment summary + form block from the linked `RawMessage`:

```python
def _message_extras(db: Session, m: EventMessage) -> dict:
    """Attachment summary + received-form metadata for a message, from its
    linked RawMessage. Empty/None for outbound or attachment-less messages."""
    from backend.modules.checkins.models import RawMessage, RawMessageAttachment
    from backend.modules.checkins.message_parser import find_form_xml, extract_form_xml
    import xml.etree.ElementTree as ET

    if m.raw_message_id is None:
        return {"attachments": [], "form": None}
    atts = db.query(RawMessageAttachment).filter_by(raw_message_id=m.raw_message_id).all()
    summary = [
        {"id": a.id, "filename": a.filename, "content_type": a.content_type, "size": len(a.data)}
        for a in atts
    ]
    raw = db.get(RawMessage, m.raw_message_id)
    att_dicts = [{"filename": a.filename, "content_type": a.content_type, "data": a.data} for a in atts]
    xml_text = find_form_xml(att_dicts, raw.body if raw else "")
    form = None
    if xml_text:
        try:
            root = ET.fromstring(xml_text)
            df = root.find(".//form_parameters/display_form")
            rt = root.find(".//form_parameters/reply_template")
            form = {
                "is_form": True,
                "display_form": (df.text or "").strip() if df is not None else "",
                "reply_template": (rt.text or "").strip() if rt is not None else "",
            }
        except ET.ParseError:
            form = None
    return {"attachments": summary, "form": form}
```

Change `_message_to_response` to accept the extras (keep it pure — the route computes extras and merges):

```python
def _message_to_response(m: EventMessage, extras: dict | None = None) -> dict:
    resp = {
        # ... all existing keys unchanged ...
    }
    resp["attachments"] = (extras or {}).get("attachments", [])
    resp["form"] = (extras or {}).get("form")
    return resp
```

In `list_messages_route`, build the list with extras:

```python
    return {
        "messages": [_message_to_response(m, _message_extras(db, m)) for m in messages],
        "latest_msg_seq": event.msg_seq,
        "messaging_configured": messaging_configured,
    }
```

Also pass `_message_extras(db, message)` at the compose (`_message_to_response(message, _message_extras(db, message))`) and patch call sites so the shape is consistent (outbound → empty attachments, null form).

Append the download route:

```python
from fastapi.responses import Response


@events_router.get("/{event_id}/messages/{message_id}/attachments/{attachment_id}")
async def download_attachment_route(
    event_id: int,
    message_id: int,
    attachment_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.checkins.models import RawMessageAttachment

    _get_event_or_404(db, ctx.net.id, event_id)
    message = (
        db.query(EventMessage)
        .filter(EventMessage.id == message_id, EventMessage.event_id == event_id)
        .one_or_none()
    )
    if message is None or message.raw_message_id is None:
        raise HTTPException(status_code=404, detail="Not found")
    att = (
        db.query(RawMessageAttachment)
        .filter(RawMessageAttachment.id == attachment_id,
                RawMessageAttachment.raw_message_id == message.raw_message_id)
        .one_or_none()
    )
    if att is None:
        raise HTTPException(status_code=404, detail="Not found")
    # Sanitize the download filename; never serve the claimed content-type
    # (a hostile form XML must download, never render).
    safe_name = att.filename.replace("\r", "").replace("\n", "").replace('"', "")
    return Response(
        content=att.data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )
```

- [ ] **Step 8: Run API tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_message_attachments_api.py tests/test_event_message_routes.py -q`
Expected: all pass (the SP3 message-route tests still pass with the new keys present).

- [ ] **Step 9: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/checkins/message_parser.py backend/modules/events/routes.py tests/test_find_form_xml.py tests/test_event_message_attachments_api.py
git commit -m "feat(events): expose message attachments, form metadata, and download route"
```

---

### Task 6: Frontend — attachment indicator & download

**Files:**
- Modify: `frontend/src/types/index.ts` (extend `EventMessage`)
- Modify: `frontend/src/api/events.ts` (attachment download URL helper)
- Modify: `frontend/src/pages/events/MessagesPanel.tsx` (📎 indicator + download links)

**Interfaces:**
- Consumes: Task 5 payload (`attachments`, `form`).
- Produces: `EventMessage` type gains `attachments` + `form`; a 📎 indicator on rows with attachments and download links in the expanded view.

- [ ] **Step 1: Extend the type**

In `frontend/src/types/index.ts`, add to the `EventMessage` interface:

```typescript
  attachments: EventMessageAttachment[];
  form: EventReceivedForm | null;
```

and add:

```typescript
export interface EventMessageAttachment {
  id: number;
  filename: string;
  content_type: string;
  size: number;
}

export interface EventReceivedForm {
  is_form: boolean;
  display_form: string;
  reply_template: string;
}
```

- [ ] **Step 2: Add the download URL helper**

In `frontend/src/api/events.ts`, append:

```typescript
export function eventAttachmentUrl(
  eventId: number,
  messageId: number,
  attachmentId: number,
  netSlug: string,
): string {
  return `/api/nets/${netSlug}/events/${eventId}/messages/${messageId}/attachments/${attachmentId}`;
}
```

(This is a plain URL for an `<a href download>` — the browser fetches it with the session cookie; no `apiFetch` needed.)

- [ ] **Step 3: Add the indicator + download links to MessagesPanel**

In `frontend/src/pages/events/MessagesPanel.tsx`, import `eventAttachmentUrl`. On the message row (next to the subject), add a paperclip when attachments exist:

```tsx
                {m.attachments.length > 0 && <span title="Has attachments">📎</span>}
```

In the expanded message view (where the body `<pre>` is), after the body, list downloadable attachments (and a form hint):

```tsx
                  {m.form?.is_form && (
                    <div className="text-xs text-accent mt-1">
                      Winlink form: {m.form.display_form}
                    </div>
                  )}
                  {m.attachments.length > 0 && (
                    <div className="flex flex-col gap-0.5 mt-2">
                      {m.attachments.map((a) => (
                        <a
                          key={a.id}
                          href={eventAttachmentUrl(event.id, m.id, a.id, netSlug)}
                          download={a.filename}
                          className="text-xs text-accent hover:underline"
                        >
                          📎 {a.filename} ({Math.ceil(a.size / 1024)} KB)
                        </a>
                      ))}
                    </div>
                  )}
```

(Use the panel's existing `event` and `netSlug` in scope.)

- [ ] **Step 4: Build check**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: builds cleanly.

- [ ] **Step 5: Full backend suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add frontend/src/types/index.ts frontend/src/api/events.ts frontend/src/pages/events/MessagesPanel.tsx
git commit -m "feat(events): attachment indicator and download in the Messages panel"
```

- [ ] **Step 6: Manual smoke test (human checkpoint)**

With `./run-dev.sh` and a net configured with a PAT mailbox: drop a `.b2f` into `{mailbox}/in` that carries an `RMS_Express_Form_*.xml` attachment addressed to the net during an active event; "Check mail now" → the message shows a 📎, the expanded view lists the attachment (downloadable) and "Winlink form: <name>". Confirm a plain-text message shows no 📎 and no form hint. Confirm downloading the form XML returns the bytes as a download (not rendered).
