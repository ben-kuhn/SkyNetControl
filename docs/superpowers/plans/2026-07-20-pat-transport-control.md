# PAT Transport Control (SP5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive PAT over its HTTP API — migrate outbound/inbound off the file handoff and add operator-triggered radio connections with live session progress.

**Architecture:** A single HTTP client seam (`pat_client.py`) wraps PAT's API; `WinlinkBackend` posts outbound there (status `QUEUED`), the scanner fetches inbound there, and a background session engine (`pat_session.py`) fires `/api/connect`, consumes PAT's `/ws` for live progress, and reconciles `QUEUED → SENT`. Everything is gated by `pat_transport_enabled`, falling back to today's file handoff when off.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (mapped_column), Alembic, httpx (sync + async, already a dep), `websockets` (new dep, for `/ws`), React 19 + TypeScript + Tailwind, pytest.

## Global Constraints

- Every PAT HTTP call goes through `backend/integrations/winlink/pat_client.py` — no other module builds PAT URLs or reads PAT auth. Tests mock this seam (httpx `MockTransport` / monkeypatched `stream_status`); **no live radio or PAT in CI**.
- `pat_transport_enabled` (per-net, global fallback) governs everything: when false, outbound writes the `.b2f` file and the scanner reads the mailbox dir exactly as today (zero behavior change). When true, both use PAT's HTTP API.
- Secrets (`pat_http_password`, `pat_http_token`) are encrypted at rest via `backend/auth/secret_box.py` `encrypt()`/`decrypt()`; config-read routes never return them in plaintext (write-only, like SMTP password / OAuth secrets).
- New `DeliveryStatus` value is exactly `QUEUED = "queued"`. Status flow for winlink-over-HTTP: `PENDING` → (`POST` ok) `QUEUED` → (connect reconcile) `SENT`, or `FAILED`.
- Connect is single-flight: one `pat_connection_sessions` row in a non-terminal status at a time; a second trigger returns HTTP 409.
- All new API routes are under `/api/nets/{net_slug}/...` and gated `require_net_role(NetRole.NET_CONTROL)`.
- Ruff: line-length 120, `select = ["E", "F"]`. Production code has no per-file ignores. Run `nix-shell --run "ruff check"` before every commit.
- Toolchain is via nix-shell: backend tests `.venv/bin/pytest -q`; frontend build `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`.
- New Python dep `websockets>=12.0` is added to `pyproject.toml` `[project.dependencies]` in Task 1; after adding it run `nix-shell --run :` so the venv reinstalls before using it.

## File Structure

- `backend/integrations/winlink/pat_client.py` (new) — HTTP client seam: outbound post, mailbox list/get/attachment, connect/disconnect, status, connect_aliases, rmslist, `stream_status` (`/ws`). Typed errors.
- `backend/integrations/winlink/pat_config.py` (new) — resolve PAT HTTP settings (base URL, auth, timeout, enabled) per-net with global fallback + secret decryption; build the client.
- `backend/integrations/winlink/pat_connect.py` (new) — pure connect-URL resolution (alias→URL, structured mode/gateway/freq→URL) + connect-options assembly.
- `backend/integrations/winlink/pat_session.py` (new) — background session engine: lifecycle, single-flight, connect, `/ws` consumer, reconcile, timeout, abort; task registry for lifespan shutdown.
- `backend/integrations/winlink/models.py` (new) — `PatConnectionSession` model.
- `backend/integrations/winlink/pat_inbound.py` (new) — fetch inbound via `pat_client`, adapt to the `read_message_file` dict shape.
- `backend/integrations/delivery/models.py` (modify) — add `DeliveryStatus.QUEUED`, `DeliveryLog.pat_session_id`, `DeliveryLog.pat_mid`.
- `backend/integrations/delivery/backends/winlink.py` (modify) — post via PAT HTTP when enabled; `QUEUED` + `pat_mid`; file fallback.
- `backend/integrations/delivery/service.py` (modify) — treat winlink `QUEUED` as a non-failure "queued" outcome; thread `net_id` into config building for PAT.
- `backend/integrations/scanner/service.py` (modify) — when transport enabled, source inbound from `pat_inbound` instead of `read_mailbox`.
- `backend/modules/events/pat_routes.py` (new) — event + net scoped PAT routes (connect, session poll, abort, connect-options, test).
- `backend/app.py` (modify) — register `pat_session` task-registry shutdown in lifespan.
- `alembic/versions/<rev>_add_pat_transport.py` (new) — `pat_connection_sessions` table + `delivery_logs` columns + `QUEUED` enum value (batched).
- Frontend: `frontend/src/types/index.ts`, `frontend/src/api/events.ts` (modify); `frontend/src/hooks/usePatSession.ts` (new); `frontend/src/pages/events/PatConnectModal.tsx` (new); `frontend/src/pages/events/MessagesPanel.tsx` (modify); net admin config UI (modify).

---

### Task 1: PAT HTTP client seam (`pat_client.py`)

**Files:**
- Create: `backend/integrations/winlink/pat_client.py`
- Create: `tests/test_pat_client.py`
- Modify: `pyproject.toml` (add `websockets>=12.0`)

**Interfaces:**
- Produces: `PatClient` (constructed with `base_url: str`, `auth: PatAuth | None`, `timeout: float`), methods `post_outbound(to, subject, body, cc, attachments) -> str` (returns PAT message id), `list_mailbox(box) -> list[dict]`, `get_message(box, mid) -> dict`, `get_attachment(box, mid, name) -> bytes`, `connect(connect_url) -> bool`, `disconnect() -> None`, `status() -> dict`, `connect_aliases() -> dict[str, str]`, `rmslist() -> list[dict]`, `async stream_status() -> AsyncIterator[dict]`. Dataclass `PatAuth(mode, username, password, token)`. Exceptions `PatUnavailable`, `PatConnectError`.

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml` `[project.dependencies]`, add after the `httpx` line:
```toml
    "websockets>=12.0",
```
Then run `nix-shell --run :` to reinstall the venv. Verify: `.venv/bin/python -c "import websockets; print(websockets.__version__)"`.

- [ ] **Step 2: Write failing tests**

```python
# tests/test_pat_client.py
import httpx
import pytest

from backend.integrations.winlink.pat_client import (
    PatAuth, PatClient, PatUnavailable, PatConnectError,
)


def _client(handler, auth=None):
    transport = httpx.MockTransport(handler)
    c = PatClient("http://pat.test:8080", auth=auth, timeout=5.0)
    c._transport = transport  # test seam: injected transport for the sync httpx.Client
    return c


def test_post_outbound_sends_multipart_and_returns_mid():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(200, json={"MID": "ABC123"})

    c = _client(handler)
    mid = c.post_outbound(
        to="KE0XYZ", subject="Hi", body="hello", cc=[],
        attachments=[{"filename": "RMS_Express_Form_ICS213.xml", "content_type": "application/octet-stream", "data": b"<x/>"}],
    )
    assert mid == "ABC123"
    assert seen["url"] == "http://pat.test:8080/api/mailbox/out"
    assert seen["method"] == "POST"
    assert "multipart/form-data" in seen["content_type"]
    assert b"KE0XYZ" in seen["body"]
    assert b"RMS_Express_Form_ICS213.xml" in seen["body"]


def test_connect_success_and_failure():
    def ok(request): return httpx.Response(200, json={"success": True})
    assert _client(ok).connect("telnet:///") is True

    def bad(request): return httpx.Response(200, json={"success": False})
    with pytest.raises(PatConnectError):
        _client(bad).connect("telnet:///")


def test_transport_error_maps_to_unavailable():
    def boom(request): raise httpx.ConnectError("refused")
    with pytest.raises(PatUnavailable):
        _client(boom).status()


def test_basic_auth_header_injected():
    seen = {}
    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})
    _client(handler, auth=PatAuth("basic", "op", "pw", "")).status()
    assert seen["auth"].startswith("Basic ")


def test_token_auth_header_injected():
    seen = {}
    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})
    _client(handler, auth=PatAuth("token", "", "", "tok123")).status()
    assert seen["auth"] == "Bearer tok123"


def test_connect_aliases_and_rmslist_parse():
    def handler(request):
        if request.url.path == "/api/config/connect_aliases":
            return httpx.Response(200, json={"gw1": "ardop:///KE0GW?freq=7100"})
        if request.url.path == "/api/rmslist":
            return httpx.Response(200, json=[{"callsign": "KE0GW", "modes": "ARDOP", "dial": 7100000}])
        return httpx.Response(404)
    c = _client(handler)
    assert c.connect_aliases() == {"gw1": "ardop:///KE0GW?freq=7100"}
    assert c.rmslist()[0]["callsign"] == "KE0GW"
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/test_pat_client.py -q`
Expected: FAIL (module not found).

- [ ] **Step 4: Implement `pat_client.py`**

```python
# backend/integrations/winlink/pat_client.py
"""HTTP client seam for PAT's Winlink API. The single place that builds PAT
URLs, injects auth, and maps transport errors. Every consumer and every test
goes through this module — no live radio in CI."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import AsyncIterator

import httpx


@dataclass
class PatAuth:
    mode: str  # "none" | "basic" | "token"
    username: str = ""
    password: str = ""
    token: str = ""


class PatUnavailable(Exception):
    """PAT could not be reached (down, bad URL, auth rejected, transport error)."""


class PatConnectError(Exception):
    """PAT accepted the request but the radio connect attempt failed."""


def _auth_headers(auth: PatAuth | None) -> dict[str, str]:
    if auth is None or auth.mode == "none":
        return {}
    if auth.mode == "basic":
        raw = f"{auth.username}:{auth.password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
    if auth.mode == "token":
        return {"Authorization": f"Bearer {auth.token}"}
    return {}


class PatClient:
    def __init__(self, base_url: str, auth: PatAuth | None = None, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout
        self._transport: httpx.BaseTransport | None = None  # test injection seam

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers=_auth_headers(self.auth),
            timeout=self.timeout,
            transport=self._transport,
        )

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            with self._client() as c:
                resp = c.request(method, path, **kw)
                resp.raise_for_status()
                return resp
        except httpx.HTTPStatusError as exc:
            raise PatUnavailable(f"PAT {method} {path} -> {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise PatUnavailable(f"PAT {method} {path} unreachable: {exc}") from exc

    def status(self) -> dict:
        return self._request("GET", "/api/status").json()

    def connect_aliases(self) -> dict[str, str]:
        return self._request("GET", "/api/config/connect_aliases").json() or {}

    def rmslist(self) -> list[dict]:
        return self._request("GET", "/api/rmslist").json() or []

    def list_mailbox(self, box: str) -> list[dict]:
        return self._request("GET", f"/api/mailbox/{box}").json() or []

    def get_message(self, box: str, mid: str) -> dict:
        return self._request("GET", f"/api/mailbox/{box}/{mid}").json()

    def get_attachment(self, box: str, mid: str, name: str) -> bytes:
        return self._request("GET", f"/api/mailbox/{box}/{mid}/{name}").content

    def post_outbound(self, to: str, subject: str, body: str, cc: list[str],
                      attachments: list[dict]) -> str:
        data = [("to", to), ("subject", subject), ("body", body)]
        data += [("cc", c) for c in cc]
        files = [
            ("attachment", (a["filename"], a["data"],
                            a.get("content_type", "application/octet-stream")))
            for a in attachments
        ]
        resp = self._request("POST", "/api/mailbox/out", data=data, files=files or None)
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return ""
        if isinstance(payload, dict):
            return str(payload.get("MID") or payload.get("mid") or "")
        return ""

    def connect(self, connect_url: str) -> bool:
        resp = self._request("GET", "/api/connect", params={"url": connect_url})
        try:
            ok = bool(resp.json().get("success", True))
        except json.JSONDecodeError:
            ok = True
        if not ok:
            raise PatConnectError(f"PAT refused connect: {connect_url}")
        return True

    def disconnect(self) -> None:
        self._request("GET", "/api/disconnect")

    async def stream_status(self) -> AsyncIterator[dict]:
        """Yield PAT status/notification frames from the /ws websocket. Replaced
        wholesale in tests with a canned async iterator."""
        import websockets  # local import: only needed when a session actually runs

        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        headers = _auth_headers(self.auth)
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            async for raw in ws:
                try:
                    yield json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_pat_client.py -q`
Expected: PASS (all 6). Then `nix-shell --run "ruff check"`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml backend/integrations/winlink/pat_client.py tests/test_pat_client.py
git commit -m "feat(pat): HTTP client seam for PAT's Winlink API"
```

---

### Task 2: PAT transport config resolution (`pat_config.py`)

**Files:**
- Create: `backend/integrations/winlink/pat_config.py`
- Create: `tests/test_pat_config.py`

**Interfaces:**
- Consumes: `get_net_config`/`set_net_config` (`backend/modules/nets/config_service.py`), `get_config_value` (`backend/config_mgmt/service.py`), `secret_box.decrypt` (`backend/auth/secret_box.py`), `PatAuth`/`PatClient` (Task 1).
- Produces: `PatHttpConfig(base_url, auth, timeout, enabled)` dataclass; `resolve_pat_config(db, net_id) -> PatHttpConfig`; `build_pat_client(cfg) -> PatClient`; `pat_transport_enabled(db, net_id) -> bool`. Config-key constants `PAT_HTTP_KEYS`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pat_config.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import secret_box
from backend.db import Base
from backend.modules.nets.models import Net
from backend.modules.nets.config_service import set_net_config
from backend.integrations.winlink.pat_config import (
    resolve_pat_config, pat_transport_enabled,
)

secret_box.install_key_material("test-secret")


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    net = Net(slug="t", name="T")
    db.add(net); db.flush()
    return db, net.id


def test_defaults_disabled():
    db, net_id = _db()
    cfg = resolve_pat_config(db, net_id)
    assert cfg.enabled is False
    assert cfg.base_url == ""
    assert pat_transport_enabled(db, net_id) is False


def test_basic_auth_decrypts_password():
    db, net_id = _db()
    set_net_config(db, net_id, "pat_transport_enabled", "true")
    set_net_config(db, net_id, "pat_http_base_url", "http://shack:8080")
    set_net_config(db, net_id, "pat_http_auth_mode", "basic")
    set_net_config(db, net_id, "pat_http_username", "op")
    set_net_config(db, net_id, "pat_http_password", secret_box.encrypt("s3cret"))
    cfg = resolve_pat_config(db, net_id)
    assert cfg.enabled is True
    assert cfg.base_url == "http://shack:8080"
    assert cfg.auth.mode == "basic"
    assert cfg.auth.username == "op"
    assert cfg.auth.password == "s3cret"


def test_timeout_default_and_override():
    db, net_id = _db()
    assert resolve_pat_config(db, net_id).timeout == 15.0
    set_net_config(db, net_id, "pat_http_timeout_seconds", "30")
    assert resolve_pat_config(db, net_id).timeout == 30.0
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_pat_config.py -q` — FAIL (module missing).

- [ ] **Step 3: Implement `pat_config.py`**

```python
# backend/integrations/winlink/pat_config.py
"""Resolve PAT HTTP transport settings (per-net with global fallback), decrypt
secrets, and construct a configured PatClient."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.auth import secret_box
from backend.config_mgmt.service import get_config_value
from backend.modules.nets.config_service import get_net_config
from backend.integrations.winlink.pat_client import PatAuth, PatClient

PAT_HTTP_KEYS = (
    "pat_transport_enabled",
    "pat_http_base_url",
    "pat_http_auth_mode",
    "pat_http_username",
    "pat_http_password",
    "pat_http_token",
    "pat_http_timeout_seconds",
)


@dataclass
class PatHttpConfig:
    base_url: str
    auth: PatAuth
    timeout: float
    enabled: bool


def _get(db: Session, net_id: int, key: str, default: str = "") -> str:
    val = get_net_config(db, net_id, key)
    if val is None:
        val = get_config_value(db, key)
    return val if val is not None else default


def resolve_pat_config(db: Session, net_id: int) -> PatHttpConfig:
    enabled = _get(db, net_id, "pat_transport_enabled").strip().lower() == "true"
    base_url = _get(db, net_id, "pat_http_base_url").strip()
    mode = _get(db, net_id, "pat_http_auth_mode", "none").strip().lower() or "none"
    username = _get(db, net_id, "pat_http_username")
    password = secret_box.decrypt(_get(db, net_id, "pat_http_password"))
    token = secret_box.decrypt(_get(db, net_id, "pat_http_token"))
    raw_timeout = _get(db, net_id, "pat_http_timeout_seconds", "15").strip()
    try:
        timeout = float(raw_timeout)
    except ValueError:
        timeout = 15.0
    return PatHttpConfig(
        base_url=base_url,
        auth=PatAuth(mode=mode, username=username, password=password, token=token),
        timeout=timeout,
        enabled=enabled,
    )


def pat_transport_enabled(db: Session, net_id: int) -> bool:
    cfg = resolve_pat_config(db, net_id)
    return cfg.enabled and bool(cfg.base_url)


def build_pat_client(cfg: PatHttpConfig) -> PatClient:
    return PatClient(cfg.base_url, auth=cfg.auth, timeout=cfg.timeout)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_pat_config.py -q` — PASS. Then `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/winlink/pat_config.py tests/test_pat_config.py
git commit -m "feat(pat): resolve PAT HTTP transport config with secret decryption"
```

---

### Task 3: Data model + migration (session table, QUEUED, delivery columns)

**Files:**
- Create: `backend/integrations/winlink/models.py`
- Modify: `backend/integrations/delivery/models.py`
- Create: `alembic/versions/a1b2c3d4e5f6_add_pat_transport.py`
- Create: `tests/test_pat_models.py`

**Interfaces:**
- Produces: `PatConnectionSession` (SQLAlchemy model, table `pat_connection_sessions`) with columns per the spec; `PatSessionStatus` str-enum (`CONNECTING`/`CONNECTED`/`SYNCING`/`COMPLETED`/`FAILED`/`ABORTED`); `DeliveryStatus.QUEUED = "queued"`; `DeliveryLog.pat_session_id: int | None`, `DeliveryLog.pat_mid: str | None`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pat_models.py
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.winlink.models import PatConnectionSession, PatSessionStatus


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_queued_status_exists():
    assert DeliveryStatus.QUEUED.value == "queued"


def test_session_row_roundtrip():
    db = _db()
    s = PatConnectionSession(
        net_id=1, event_id=None, connect_url="telnet:///",
        method_label="alias: telnet", status=PatSessionStatus.CONNECTING,
        sent_count=0, received_count=0, events=[], actor="W0NC",
        started_at=datetime.now(tz=timezone.utc),
    )
    db.add(s); db.commit()
    got = db.query(PatConnectionSession).one()
    assert got.status == PatSessionStatus.CONNECTING
    assert got.events == []


def test_delivery_log_pat_columns():
    db = _db()
    log = DeliveryLog(
        content_type="event_message", content_id=1, backend="winlink",
        status=DeliveryStatus.QUEUED, created_at=datetime.now(tz=timezone.utc),
        pat_session_id=None, pat_mid="ABC123",
    )
    db.add(log); db.commit()
    assert db.query(DeliveryLog).one().pat_mid == "ABC123"
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_pat_models.py -q` — FAIL.

- [ ] **Step 3: Add `QUEUED` + delivery columns**

In `backend/integrations/delivery/models.py`, add to `DeliveryStatus`:
```python
    QUEUED = "queued"
```
and to `DeliveryLog` (after `sent_at`):
```python
    pat_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("pat_connection_sessions.id", ondelete="SET NULL"), nullable=True
    )
    pat_mid: Mapped[str | None] = mapped_column(String(64), nullable=True)
```
Ensure `ForeignKey` and `String` are imported at the top of the file.

- [ ] **Step 4: Create `PatConnectionSession` model**

```python
# backend/integrations/winlink/models.py
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db import Base


class PatSessionStatus(str, enum.Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    SYNCING = "syncing"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class PatConnectionSession(Base):
    __tablename__ = "pat_connection_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    net_id: Mapped[int] = mapped_column(ForeignKey("nets.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    connect_url: Mapped[str] = mapped_column(String(512), nullable=False)
    method_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[PatSessionStatus] = mapped_column(nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    actor: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Import the new model where model discovery happens (mirror how `backend/integrations/delivery/models.py` is imported at app/Base setup — check `backend/db.py` or the models `__init__` and add `backend.integrations.winlink.models` to the same import site so `Base.metadata` sees the table).

- [ ] **Step 5: Write the migration (batched for the enum + columns)**

Run `.venv/bin/alembic heads` to confirm the current head is `e6f3a2b1c4d8`, then create `alembic/versions/a1b2c3d4e5f6_add_pat_transport.py`:
```python
"""add pat transport

Revision ID: a1b2c3d4e5f6
Revises: e6f3a2b1c4d8
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e6f3a2b1c4d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pat_connection_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("net_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("connect_url", sa.String(length=512), nullable=False),
        sa.Column("method_label", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("received_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["net_id"], ["nets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # delivery_logs.status is a VARCHAR+CHECK enum on SQLite; recreate the check to
    # admit "queued", and add the two PAT columns.
    with op.batch_alter_table("delivery_logs") as batch:
        batch.add_column(sa.Column("pat_session_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pat_mid", sa.String(length=64), nullable=True))
        batch.create_foreign_key(
            "fk_delivery_pat_session", "pat_connection_sessions",
            ["pat_session_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("delivery_logs") as batch:
        batch.drop_constraint("fk_delivery_pat_session", type_="foreignkey")
        batch.drop_column("pat_mid")
        batch.drop_column("pat_session_id")
    op.drop_table("pat_connection_sessions")
```
Note: the `DeliveryStatus` enum is declared without a DB-level `CHECK` (SQLAlchemy `Enum` with `native_enum=False` renders plain VARCHAR only if configured; the existing column is a bare mapped enum stored as string). If `alembic upgrade` on a fresh DB errors on a CHECK constraint for `status`, extend the batch block to `batch.alter_column("status", existing_type=sa.String(length=20))` to drop/rebuild it. Verify empirically in Step 6.

- [ ] **Step 6: Run tests + migration chain**

Run: `.venv/bin/pytest tests/test_pat_models.py -q` — PASS.
Run: `SKYNET_DATABASE_URL="sqlite:////tmp/claude-sp5-mig.db" .venv/bin/alembic upgrade head && SKYNET_DATABASE_URL="sqlite:////tmp/claude-sp5-mig.db" .venv/bin/alembic downgrade -1 && rm -f /tmp/claude-sp5-mig.db` — clean up and down. Then `nix-shell --run "ruff check"`.

- [ ] **Step 7: Commit**

```bash
git add backend/integrations/winlink/models.py backend/integrations/delivery/models.py alembic/versions/a1b2c3d4e5f6_add_pat_transport.py tests/test_pat_models.py
git commit -m "feat(pat): connection-session model, QUEUED status, delivery pat columns"
```

---

### Task 4: Outbound migration — `WinlinkBackend` posts via PAT HTTP

**Files:**
- Modify: `backend/integrations/delivery/backends/winlink.py`
- Modify: `backend/integrations/delivery/service.py` (config building + QUEUED handling)
- Modify: `tests/test_delivery_winlink.py`
- Create: `tests/test_delivery_winlink_http.py`

**Interfaces:**
- Consumes: `PatClient.post_outbound` (Task 1), `resolve_pat_config`/`build_pat_client` (Task 2), `DeliveryStatus.QUEUED`/`DeliveryLog.pat_mid` (Task 3).
- Produces: `WinlinkBackend.send` returns `DeliveryResult(success, error, queued: bool, pat_mid: str | None)` — the `DeliveryResult` dataclass gains `queued: bool = False` and `pat_mid: str | None = None`. When `config["pat_http"]` (a resolved `PatHttpConfig`) is present and enabled, posts via HTTP and returns `queued=True`; otherwise writes the file as today.

- [ ] **Step 1: Extend `DeliveryResult`**

In `backend/integrations/delivery/backends/base.py`:
```python
@dataclass
class DeliveryResult:
    success: bool
    error: str | None
    queued: bool = False
    pat_mid: str | None = None
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_delivery_winlink_http.py
import httpx

from backend.integrations.winlink.pat_config import PatHttpConfig
from backend.integrations.winlink.pat_client import PatAuth, PatClient
from backend.integrations.delivery.backends.winlink import WinlinkBackend


def _cfg_with_client(handler):
    client = PatClient("http://pat.test", auth=PatAuth("none"), timeout=5.0)
    client._transport = httpx.MockTransport(handler)
    cfg = PatHttpConfig(base_url="http://pat.test", auth=PatAuth("none"), timeout=5.0, enabled=True)
    return cfg, client


def test_send_posts_via_http_and_returns_queued():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"MID": "M1"})

    cfg, client = _cfg_with_client(handler)
    result = WinlinkBackend().send("Subj", "Body", {
        "target_address": "KE0XYZ", "callsign": "W0NE",
        "attachments": [{"filename": "f.xml", "content_type": "application/octet-stream", "data": b"<x/>"}],
        "pat_http": cfg, "pat_client": client,
    })
    assert result.success is True
    assert result.queued is True
    assert result.pat_mid == "M1"
    assert seen["path"] == "/api/mailbox/out"
    assert b"KE0XYZ" in seen["body"]


def test_send_falls_back_to_file_when_disabled(tmp_path):
    result = WinlinkBackend().send("Subj", "Body", {
        "mailbox_path": str(tmp_path), "target_address": "KE0XYZ", "callsign": "W0NE",
    })
    assert result.success is True
    assert result.queued is False
    assert (tmp_path / "out").exists()


def test_http_failure_returns_error_not_success():
    def handler(request):
        raise httpx.ConnectError("refused")

    cfg, client = _cfg_with_client(handler)
    result = WinlinkBackend().send("Subj", "Body", {
        "target_address": "KE0XYZ", "callsign": "W0NE",
        "pat_http": cfg, "pat_client": client,
    })
    assert result.success is False
    assert result.queued is False
    assert result.error
```

- [ ] **Step 3: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_delivery_winlink_http.py -q` — FAIL.

- [ ] **Step 4: Implement the HTTP branch in `WinlinkBackend.send`**

Prepend to the existing `send` body (keep the existing file-writing code as the `else`/fallback):
```python
from backend.integrations.winlink.pat_client import PatUnavailable

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        pat_http = config.get("pat_http")
        if pat_http is not None and getattr(pat_http, "enabled", False):
            client = config.get("pat_client") or build_pat_client(pat_http)
            attachments = config.get("attachments") or []
            atts = [
                {"filename": a.filename, "content_type": a.content_type, "data": a.data}
                if hasattr(a, "filename") else a
                for a in attachments
            ]
            try:
                mid = client.post_outbound(
                    to=config["target_address"], subject=subject, body=body,
                    cc=[], attachments=atts,
                )
            except PatUnavailable as exc:
                return DeliveryResult(success=False, error=str(exc))
            return DeliveryResult(success=True, error=None, queued=True, pat_mid=mid or None)
        # --- existing file-based handoff below (unchanged) ---
```
Add `from backend.integrations.winlink.pat_config import build_pat_client` to the imports. Note the codec attachments (`B2FAttachment`) are normalized to dicts for `post_outbound`.

- [ ] **Step 5: Thread PAT config + QUEUED into the delivery service**

In `backend/integrations/delivery/service.py`, where the winlink backend config is built (`_build_config`), add the resolved PAT config when the backend is `winlink`:
```python
    if backend_name == "winlink":
        from backend.integrations.winlink.pat_config import resolve_pat_config
        cfg["pat_http"] = resolve_pat_config(db, net_id)
```
Where the `DeliveryResult` is turned into a `DeliveryLog` status, treat `queued` as its own status and persist `pat_mid`:
```python
    if result.success and result.queued:
        log.status = DeliveryStatus.QUEUED
        log.pat_mid = result.pat_mid
        log.error_message = None
    elif result.success:
        log.status = DeliveryStatus.SENT
        log.sent_at = datetime.now(tz=timezone.utc)
    else:
        log.status = DeliveryStatus.FAILED
        log.error_message = result.error
```
`dispatch_delivery` should count `QUEUED` as a non-failure (the outbound is accepted; delivery is confirmed later by a connect). Ensure its return-value logic treats `result.success` (which is True for queued) as success.

- [ ] **Step 6: Update the existing winlink test for the fallback shape**

In `tests/test_delivery_winlink.py`, the existing success test asserts a `.b2f` on disk. That path is now the `pat_http`-absent fallback — the tests still pass unchanged (no `pat_http` key → file branch). Run them to confirm; if any asserted `DeliveryResult(success, error)` positionally, update to keyword/`.success`.

- [ ] **Step 7: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_delivery_winlink_http.py tests/test_delivery_winlink.py tests/test_delivery_service.py -q` — PASS. Then `nix-shell --run "ruff check"`.

- [ ] **Step 8: Commit**

```bash
git add backend/integrations/delivery/backends/base.py backend/integrations/delivery/backends/winlink.py backend/integrations/delivery/service.py tests/test_delivery_winlink_http.py tests/test_delivery_winlink.py
git commit -m "feat(pat): outbound posts via PAT HTTP with QUEUED status + pat_mid"
```

---

### Task 5: Inbound migration — fetch via PAT HTTP (`pat_inbound.py`)

**Files:**
- Create: `backend/integrations/winlink/pat_inbound.py`
- Modify: `backend/integrations/scanner/service.py`
- Create: `tests/test_pat_inbound.py`

**Interfaces:**
- Consumes: `PatClient.list_mailbox`/`get_message`/`get_attachment` (Task 1), `resolve_pat_config`/`pat_transport_enabled`/`build_pat_client` (Task 2).
- Produces: `fetch_inbound_messages(client) -> list[dict]` returning dicts in the exact `read_message_file` shape (`path`, `message_id`, `from_address`, `to_address`, `subject`, `received_at`, `body`, `attachments=[{filename, content_type, data}]`) so `scan_and_import_messages` consumes them unchanged. `scan_one` uses this source when `pat_transport_enabled`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pat_inbound.py
from datetime import datetime

import httpx

from backend.integrations.winlink.pat_client import PatAuth, PatClient
from backend.integrations.winlink.pat_inbound import fetch_inbound_messages


def _client(handler):
    c = PatClient("http://pat.test", auth=PatAuth("none"), timeout=5.0)
    c._transport = httpx.MockTransport(handler)
    return c


def test_fetch_maps_messages_and_attachments():
    def handler(request):
        p = request.url.path
        if p == "/api/mailbox/in":
            return httpx.Response(200, json=[{"MID": "M1"}])
        if p == "/api/mailbox/in/M1":
            return httpx.Response(200, json={
                "MID": "M1", "From": "W0ABC@winlink.org", "To": "W0NE@winlink.org",
                "Subject": "Check-in", "Date": "2026-07-20T18:30:00Z",
                "Body": "all good",
                "Files": [{"Name": "RMS_Express_Form_ICS213.xml"}],
            })
        if p == "/api/mailbox/in/M1/RMS_Express_Form_ICS213.xml":
            return httpx.Response(200, content=b"<RMS_Express_Form/>")
        return httpx.Response(404)

    msgs = fetch_inbound_messages(_client(handler))
    assert len(msgs) == 1
    m = msgs[0]
    assert m["message_id"] == "M1"
    assert m["from_address"] == "W0ABC@winlink.org"
    assert m["to_address"] == "W0NE@winlink.org"
    assert m["subject"] == "Check-in"
    assert m["body"] == "all good"
    assert isinstance(m["received_at"], datetime)
    assert m["attachments"][0]["filename"] == "RMS_Express_Form_ICS213.xml"
    assert m["attachments"][0]["data"] == b"<RMS_Express_Form/>"


def test_fetch_skips_unparseable_message():
    def handler(request):
        if request.url.path == "/api/mailbox/in":
            return httpx.Response(200, json=[{"MID": "BAD"}])
        if request.url.path == "/api/mailbox/in/BAD":
            return httpx.Response(200, json={"MID": "BAD"})  # no From
        return httpx.Response(404)

    assert fetch_inbound_messages(_client(handler)) == []
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_pat_inbound.py -q` — FAIL.

- [ ] **Step 3: Implement `pat_inbound.py`**

```python
# backend/integrations/winlink/pat_inbound.py
"""Fetch inbound Winlink messages from PAT over HTTP and adapt them into the
dict shape read_message_file produces, so the existing import path is unchanged."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.integrations.winlink.pat_client import PatClient, PatUnavailable


def _parse_date(raw: str) -> datetime:
    if not raw:
        return datetime.now(tz=timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(tz=timezone.utc)


def _adapt(client: PatClient, summary: dict) -> dict | None:
    mid = str(summary.get("MID") or summary.get("mid") or "")
    if not mid:
        return None
    msg = client.get_message("in", mid)
    from_addr = (msg.get("From") or "").strip()
    if not from_addr:
        return None
    attachments = []
    for f in msg.get("Files") or []:
        name = f.get("Name") or f.get("filename")
        if not name:
            continue
        data = client.get_attachment("in", mid, name)
        attachments.append({
            "filename": name,
            "content_type": f.get("ContentType") or "application/octet-stream",
            "data": data,
        })
    return {
        "path": f"pat-http://in/{mid}",
        "message_id": mid,
        "from_address": from_addr,
        "to_address": (msg.get("To") or "").strip(),
        "subject": (msg.get("Subject") or "").strip(),
        "received_at": _parse_date(msg.get("Date") or ""),
        "body": msg.get("Body") or "",
        "attachments": attachments,
    }


def fetch_inbound_messages(client: PatClient) -> list[dict]:
    """Return inbound messages in read_message_file shape. Best-effort: a message
    that can't be fetched/parsed is skipped, never raised."""
    try:
        summaries = client.list_mailbox("in")
    except PatUnavailable:
        raise
    out: list[dict] = []
    for summary in summaries:
        try:
            adapted = _adapt(client, summary)
        except PatUnavailable:
            raise
        except Exception:
            adapted = None
        if adapted is not None:
            out.append(adapted)
    return out
```

- [ ] **Step 4: Wire into `scan_one`**

In `backend/integrations/scanner/service.py` `scan_one`, before the existing `read_mailbox(...)` call, branch on transport:
```python
    from backend.integrations.winlink.pat_config import (
        pat_transport_enabled, resolve_pat_config, build_pat_client,
    )
    from backend.integrations.winlink.pat_inbound import fetch_inbound_messages
    from backend.integrations.winlink.pat_client import PatUnavailable

    if pat_transport_enabled(db, net_id):
        client = build_pat_client(resolve_pat_config(db, net_id))
        try:
            raw_messages = fetch_inbound_messages(client)
        except PatUnavailable:
            logger.warning("PAT unreachable during scan for net %s", net_id)
            return 0
    else:
        raw_messages = read_mailbox(mailbox, net_address)
```
Keep the rest of `scan_one` (filter by `net_address`, `scan_and_import_messages`) unchanged. Note: HTTP-sourced messages are already this net's mailbox, but keep the existing `net_address` filter for consistency (harmless — PAT's `in` box is the station's, and the filter matches the net address).

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_pat_inbound.py tests/test_scanner*.py -q` — PASS (existing scanner tests unaffected: no `pat_transport_enabled` → file path). Then `nix-shell --run "ruff check"`.

- [ ] **Step 6: Commit**

```bash
git add backend/integrations/winlink/pat_inbound.py backend/integrations/scanner/service.py tests/test_pat_inbound.py
git commit -m "feat(pat): fetch inbound via PAT HTTP, reuse existing import path"
```

---

### Task 6: Connect-method resolution (`pat_connect.py`)

**Files:**
- Create: `backend/integrations/winlink/pat_connect.py`
- Create: `tests/test_pat_connect.py`

**Interfaces:**
- Consumes: `PatClient.connect_aliases`/`rmslist` (Task 1).
- Produces: `resolve_connect_url(request, aliases) -> tuple[str, str]` returning `(connect_url, method_label)` from either `{"alias": name}` or `{"mode","gateway","freq"?}`; `build_connect_options(client) -> dict` returning `{"aliases": [{name, url}], "gateways": [{callsign, modes, freq}]}`. Raises `ValueError` on bad input (unknown alias, missing gateway).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pat_connect.py
import pytest

from backend.integrations.winlink.pat_connect import resolve_connect_url


def test_alias_resolves_to_url():
    aliases = {"gw1": "ardop:///KE0GW?freq=7100"}
    url, label = resolve_connect_url({"alias": "gw1"}, aliases)
    assert url == "ardop:///KE0GW?freq=7100"
    assert label == "alias: gw1"


def test_unknown_alias_raises():
    with pytest.raises(ValueError):
        resolve_connect_url({"alias": "nope"}, {})


def test_structured_builds_url_with_freq():
    url, label = resolve_connect_url(
        {"mode": "ardop", "gateway": "KE0GW", "freq": "7100"}, {})
    assert url == "ardop:///KE0GW?freq=7100"
    assert "KE0GW" in label and "7100" in label


def test_structured_without_freq():
    url, label = resolve_connect_url({"mode": "telnet", "gateway": "cms"}, {})
    assert url == "telnet:///cms"


def test_structured_missing_gateway_raises():
    with pytest.raises(ValueError):
        resolve_connect_url({"mode": "ardop"}, {})


def test_bad_mode_raises():
    with pytest.raises(ValueError):
        resolve_connect_url({"mode": "carrierpigeon", "gateway": "X"}, {})
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_pat_connect.py -q` — FAIL.

- [ ] **Step 3: Implement `pat_connect.py`**

```python
# backend/integrations/winlink/pat_connect.py
"""Pure resolution of an operator's connect choice into a PAT connect URL."""
from __future__ import annotations

from backend.integrations.winlink.pat_client import PatClient

VALID_MODES = ("telnet", "ardop", "vara", "varafm", "packet", "pactor")


def resolve_connect_url(request: dict, aliases: dict[str, str]) -> tuple[str, str]:
    """Return (connect_url, method_label). `request` is either {"alias": name}
    or {"mode","gateway","freq"?}."""
    alias = request.get("alias")
    if alias:
        url = aliases.get(alias)
        if not url:
            raise ValueError(f"Unknown connect alias: {alias}")
        return url, f"alias: {alias}"

    mode = (request.get("mode") or "").strip().lower()
    gateway = (request.get("gateway") or "").strip()
    freq = (request.get("freq") or "").strip()
    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported mode: {mode or '(none)'}")
    if not gateway:
        raise ValueError("Gateway is required")
    url = f"{mode}:///{gateway}"
    label = f"{mode} {gateway}"
    if freq:
        url += f"?freq={freq}"
        label += f" @ {freq}"
    return url, label


def build_connect_options(client: PatClient) -> dict:
    aliases = client.connect_aliases()
    gateways = []
    for r in client.rmslist():
        dial = r.get("dial") or r.get("Dial") or 0
        gateways.append({
            "callsign": r.get("callsign") or r.get("Callsign") or "",
            "modes": r.get("modes") or r.get("Modes") or "",
            "freq": str(dial),
        })
    return {
        "aliases": [{"name": k, "url": v} for k, v in aliases.items()],
        "gateways": gateways,
    }
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_pat_connect.py -q` — PASS. Then `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/winlink/pat_connect.py tests/test_pat_connect.py
git commit -m "feat(pat): resolve alias/structured connect choices to PAT connect URLs"
```

---

### Task 7: Session engine core — connect, reconcile, single-flight (`pat_session.py`)

**Files:**
- Create: `backend/integrations/winlink/pat_session.py`
- Create: `tests/test_pat_session.py`

**Interfaces:**
- Consumes: `PatConnectionSession`/`PatSessionStatus` (Task 3), `DeliveryLog`/`DeliveryStatus` (Task 3), `PatClient` + errors (Task 1), `scan_all_enabled` (`backend/integrations/scanner/service.py`).
- Produces: module singleton `engine` (`PatSessionEngine`) with `async start(session_factory, *, net_id, event_id, actor, connect_url, method_label, client) -> int` (raises `SessionBusy` if a session is active), `async run_session(session_factory, session_id, client, timeout) -> None`, `async abort(session_factory, session_id, client) -> None`, `async shutdown() -> None`, and `active_session_id -> int | None`. Exception `SessionBusy`. A `_reconcile_outbound(db, client, session)` helper flips `QUEUED → SENT`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pat_session.py
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.winlink.models import PatConnectionSession, PatSessionStatus
from backend.integrations.winlink.pat_client import PatConnectError, PatUnavailable
from backend.integrations.winlink import pat_session


def _factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class FakeClient:
    def __init__(self, *, out_box=None, connect_ok=True, unreachable=False):
        self._out_box = out_box if out_box is not None else []
        self._connect_ok = connect_ok
        self._unreachable = unreachable
        self.disconnected = False

    def connect(self, url):
        if self._unreachable:
            raise PatUnavailable("down")
        if not self._connect_ok:
            raise PatConnectError("no link")
        return True

    def disconnect(self):
        self.disconnected = True

    def list_mailbox(self, box):
        return self._out_box if box == "out" else []


@pytest.fixture(autouse=True)
def _reset_engine():
    pat_session.engine = pat_session.PatSessionEngine()
    yield


async def _seed_session(factory, **over):
    with factory() as db:
        s = PatConnectionSession(
            net_id=over.get("net_id", 1), event_id=None,
            connect_url="telnet:///", method_label="alias: telnet",
            status=PatSessionStatus.CONNECTING, sent_count=0, received_count=0,
            events=[], actor="W0NC", started_at=datetime.now(tz=timezone.utc),
        )
        db.add(s); db.commit()
        return s.id


async def test_successful_session_marks_completed(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(pat_session, "scan_all_enabled", lambda db, now: 2)
    sid = await _seed_session(factory)
    await pat_session.engine.run_session(factory, sid, FakeClient(), timeout=5)
    with factory() as db:
        s = db.get(PatConnectionSession, sid)
        assert s.status == PatSessionStatus.COMPLETED
        assert s.received_count == 2
        assert s.ended_at is not None


async def test_connect_failure_marks_failed(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(pat_session, "scan_all_enabled", lambda db, now: 0)
    sid = await _seed_session(factory)
    await pat_session.engine.run_session(factory, sid, FakeClient(connect_ok=False), timeout=5)
    with factory() as db:
        assert db.get(PatConnectionSession, sid).status == PatSessionStatus.FAILED


async def test_reconcile_flips_queued_to_sent(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(pat_session, "scan_all_enabled", lambda db, now: 0)
    with factory() as db:
        db.add(DeliveryLog(content_type="event_message", content_id=1, backend="winlink",
                           status=DeliveryStatus.QUEUED, pat_mid="GONE",
                           created_at=datetime.now(tz=timezone.utc)))
        db.add(DeliveryLog(content_type="event_message", content_id=2, backend="winlink",
                           status=DeliveryStatus.QUEUED, pat_mid="STILL",
                           created_at=datetime.now(tz=timezone.utc)))
        db.commit()
    sid = await _seed_session(factory)
    # PAT's out box still holds STILL; GONE was sent.
    client = FakeClient(out_box=[{"MID": "STILL"}])
    await pat_session.engine.run_session(factory, sid, client, timeout=5)
    with factory() as db:
        rows = {r.pat_mid: r.status for r in db.query(DeliveryLog).all()}
        assert rows["GONE"] == DeliveryStatus.SENT
        assert rows["STILL"] == DeliveryStatus.QUEUED
        assert db.get(PatConnectionSession, sid).sent_count == 1


async def test_single_flight_blocks_second_start(monkeypatch):
    factory = _factory()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run(f, sid, client, timeout):
        started.set()
        await release.wait()

    monkeypatch.setattr(pat_session.engine, "run_session", slow_run)
    await pat_session.engine.start(factory, net_id=1, event_id=None, actor="W0NC",
                                   connect_url="telnet:///", method_label="x", client=FakeClient())
    await started.wait()
    with pytest.raises(pat_session.SessionBusy):
        await pat_session.engine.start(factory, net_id=1, event_id=None, actor="W0NC",
                                       connect_url="telnet:///", method_label="x", client=FakeClient())
    release.set()
    await pat_session.engine.shutdown()
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_pat_session.py -q` — FAIL.

- [ ] **Step 3: Implement `pat_session.py`**

```python
# backend/integrations/winlink/pat_session.py
"""Background PAT connect/session engine. One session at a time (single radio);
runs the connect in a thread, reconciles QUEUED->SENT from PAT's outbox, imports
inbound, and records status. Live /ws progress is layered on in a later task."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.scanner.service import scan_all_enabled
from backend.integrations.winlink.models import PatConnectionSession, PatSessionStatus
from backend.integrations.winlink.pat_client import PatClient, PatConnectError, PatUnavailable

logger = logging.getLogger(__name__)


class SessionBusy(Exception):
    """A connect session is already running (single radio, single-flight)."""


def _set_status(db, session_id, status, **fields):
    s = db.get(PatConnectionSession, session_id)
    s.status = status
    for k, v in fields.items():
        setattr(s, k, v)
    db.commit()


def _reconcile_outbound(db, client: PatClient, session_id: int) -> int:
    """Flip QUEUED winlink deliveries to SENT when their pat_mid is no longer in
    PAT's out box. Returns the count flipped. Best-effort: on any PAT error the
    rows stay QUEUED (safe)."""
    try:
        still_out = {str(m.get("MID") or m.get("mid") or "") for m in client.list_mailbox("out")}
    except PatUnavailable:
        return 0
    flipped = 0
    queued = (db.query(DeliveryLog)
                .filter(DeliveryLog.backend == "winlink",
                        DeliveryLog.status == DeliveryStatus.QUEUED).all())
    for row in queued:
        if row.pat_mid and row.pat_mid not in still_out:
            row.status = DeliveryStatus.SENT
            row.sent_at = datetime.now(tz=timezone.utc)
            row.pat_session_id = session_id
            flipped += 1
    db.commit()
    return flipped


class PatSessionEngine:
    def __init__(self):
        self._active_task: asyncio.Task | None = None
        self.active_session_id: int | None = None

    async def start(self, session_factory, *, net_id, event_id, actor,
                    connect_url, method_label, client) -> int:
        if self._active_task is not None and not self._active_task.done():
            raise SessionBusy("A PAT connection is already in progress")
        with session_factory() as db:
            s = PatConnectionSession(
                net_id=net_id, event_id=event_id, connect_url=connect_url,
                method_label=method_label, status=PatSessionStatus.CONNECTING,
                sent_count=0, received_count=0, events=[], actor=actor,
                started_at=datetime.now(tz=timezone.utc),
            )
            db.add(s); db.commit()
            session_id = s.id
        self.active_session_id = session_id
        self._active_task = asyncio.create_task(
            self.run_session(session_factory, session_id, client, timeout=300)
        )
        return session_id

    async def run_session(self, session_factory, session_id, client: PatClient, timeout: int) -> None:
        try:
            with session_factory() as db:
                _set_status(db, session_id, PatSessionStatus.CONNECTED)
            try:
                await asyncio.wait_for(asyncio.to_thread(client.connect,
                                                         _connect_url(session_factory, session_id)),
                                       timeout=timeout)
            except (PatConnectError, PatUnavailable) as exc:
                with session_factory() as db:
                    _set_status(db, session_id, PatSessionStatus.FAILED,
                                error=str(exc), ended_at=datetime.now(tz=timezone.utc))
                return
            except asyncio.TimeoutError:
                try:
                    await asyncio.to_thread(client.disconnect)
                except Exception:
                    pass
                with session_factory() as db:
                    _set_status(db, session_id, PatSessionStatus.FAILED,
                                error="session timed out",
                                ended_at=datetime.now(tz=timezone.utc))
                return

            with session_factory() as db:
                _set_status(db, session_id, PatSessionStatus.SYNCING)
                sent = _reconcile_outbound(db, client, session_id)
            received = 0
            try:
                with session_factory() as db:
                    received = scan_all_enabled(db, datetime.now(tz=timezone.utc)) or 0
            except Exception:
                logger.exception("inbound import during session %s failed", session_id)
            with session_factory() as db:
                _set_status(db, session_id, PatSessionStatus.COMPLETED,
                            sent_count=sent, received_count=received,
                            ended_at=datetime.now(tz=timezone.utc))
        finally:
            self.active_session_id = None

    async def abort(self, session_factory, session_id, client: PatClient) -> None:
        try:
            await asyncio.to_thread(client.disconnect)
        except Exception:
            pass
        with session_factory() as db:
            s = db.get(PatConnectionSession, session_id)
            if s and s.status not in (PatSessionStatus.COMPLETED, PatSessionStatus.FAILED):
                _set_status(db, session_id, PatSessionStatus.ABORTED,
                            ended_at=datetime.now(tz=timezone.utc))

    async def shutdown(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()
            try:
                await self._active_task
            except (asyncio.CancelledError, Exception):
                pass
        self._active_task = None
        self.active_session_id = None


def _connect_url(session_factory, session_id: int) -> str:
    with session_factory() as db:
        return db.get(PatConnectionSession, session_id).connect_url


engine = PatSessionEngine()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_pat_session.py -q` — PASS (all 4). Then `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/winlink/pat_session.py tests/test_pat_session.py
git commit -m "feat(pat): connect/session engine with single-flight and QUEUED->SENT reconcile"
```

---

### Task 8: Live `/ws` session progress + abort refinement

**Files:**
- Modify: `backend/integrations/winlink/pat_session.py`
- Modify: `tests/test_pat_session.py`

**Interfaces:**
- Consumes: `PatClient.stream_status` (Task 1, an async iterator of PAT status frames).
- Produces: during `run_session`, a concurrent consumer appends `{ts, kind, text}` entries to `PatConnectionSession.events` and advances the phase when PAT reports link-up / traffic. New helper `_append_event(db, session_id, kind, text)`; `run_session` accepts an injectable `status_stream` coroutine (defaults to `client.stream_status`) so tests feed a canned async iterator.

- [ ] **Step 1: Write failing test (append to `tests/test_pat_session.py`)**

```python
async def test_live_events_recorded(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(pat_session, "scan_all_enabled", lambda db, now: 0)

    async def fake_stream():
        yield {"notification": {"body": "Connecting to KE0GW"}}
        yield {"status": {"connected": True}}
        yield {"notification": {"body": "Receiving message 1"}}

    sid = await _seed_session(factory)
    await pat_session.engine.run_session(
        factory, sid, FakeClient(), timeout=5, status_stream=fake_stream,
    )
    with factory() as db:
        s = db.get(PatConnectionSession, sid)
        texts = [e["text"] for e in s.events]
        assert any("Connecting to KE0GW" in t for t in texts)
        assert any("Receiving message 1" in t for t in texts)
```

- [ ] **Step 2: Run test, verify fail**

Run: `.venv/bin/pytest tests/test_pat_session.py::test_live_events_recorded -q` — FAIL (`run_session` has no `status_stream` param).

- [ ] **Step 3: Implement the consumer**

Add the helper and a status-frame normalizer to `pat_session.py`:
```python
from sqlalchemy.orm.attributes import flag_modified


def _append_event(db, session_id, kind, text):
    s = db.get(PatConnectionSession, session_id)
    events = list(s.events or [])
    events.append({"ts": datetime.now(tz=timezone.utc).isoformat(), "kind": kind, "text": text})
    s.events = events
    flag_modified(s, "events")
    db.commit()


def _frame_text(frame: dict) -> tuple[str, str] | None:
    """Reduce a PAT /ws frame to (kind, text), or None to ignore."""
    if "notification" in frame and isinstance(frame["notification"], dict):
        body = frame["notification"].get("body") or frame["notification"].get("message")
        if body:
            return "notification", str(body)
    if "status" in frame and isinstance(frame["status"], dict):
        if frame["status"].get("connected"):
            return "status", "Link established"
    if "log_line" in frame:
        return "log", str(frame["log_line"])
    return None


async def _consume_status(session_factory, session_id, status_stream, stop: asyncio.Event):
    try:
        agen = status_stream()
        async for frame in agen:
            if stop.is_set():
                break
            reduced = _frame_text(frame)
            if reduced is None:
                continue
            kind, text = reduced
            with session_factory() as db:
                _append_event(db, session_id, kind, text)
                if kind == "status" and text == "Link established":
                    s = db.get(PatConnectionSession, session_id)
                    if s.status == PatSessionStatus.CONNECTED:
                        s.status = PatSessionStatus.SYNCING
                        db.commit()
    except (asyncio.CancelledError, Exception):
        return
```

Change `run_session`'s signature to `run_session(self, session_factory, session_id, client, timeout, status_stream=None)` and, right after setting `CONNECTED`, start the consumer and stop it in `finally`:
```python
            stop = asyncio.Event()
            stream = status_stream or client.stream_status
            consumer = asyncio.create_task(_consume_status(session_factory, session_id, stream, stop))
```
Wrap the rest of the body so that in a `finally` you `stop.set()` then `consumer.cancel()` + `await` it (swallow cancellation). Keep all existing connect/reconcile/completion logic.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_pat_session.py -q` — PASS (all 5, prior 4 unaffected). Then `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/winlink/pat_session.py tests/test_pat_session.py
git commit -m "feat(pat): live /ws session progress recorded to the session event log"
```

---

### Task 9: API routes + lifespan shutdown

**Files:**
- Create: `backend/modules/events/pat_routes.py`
- Modify: `backend/app.py` (register router + session-engine shutdown)
- Create: `tests/test_pat_routes.py`

**Interfaces:**
- Consumes: `engine`/`SessionBusy` (Tasks 7–8), `resolve_pat_config`/`build_pat_client`/`pat_transport_enabled` (Task 2), `resolve_connect_url`/`build_connect_options` (Task 6), `PatConnectionSession` (Task 3), `require_net_role`/`NetContext`/`get_db_session`/`_get_event_or_404` patterns (events routes), `PatUnavailable` (Task 1).
- Produces: `pat_router = APIRouter(prefix="/api/nets/{net_slug}")` with routes `POST /events/{event_id}/pat/connect`, `POST /pat/connect` (net-scoped), `GET /pat/sessions/{session_id}`, `POST /pat/sessions/{session_id}/abort`, `GET /pat/connect-options`, `POST /pat/test`. `_session_to_response(s) -> dict`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pat_routes.py
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from backend.config import Settings
from backend.db import Base
from backend.auth import secret_box
from backend.auth.jwt import create_access_token  # adjust import to conftest's make_test_token
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.nets.config_service import set_net_config
from backend.modules.users.models import User
from backend.integrations.winlink import pat_session

secret_box.install_key_material("test-secret")
BASE = "/api/nets/t"


@pytest.fixture(autouse=True)
def _reset_engine():
    pat_session.engine = pat_session.PatSessionEngine()
    yield


@pytest.fixture
def app_with_pat(tmp_path):
    settings = Settings(database_url="sqlite:///", debug=True, jwt_secret_key="test-secret")
    app = create_app(settings=settings)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add(User(callsign="W0NC", oidc_subject="x|nc", name="NC"))
        net = Net(slug="t", name="T"); db.add(net); db.flush()
        db.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        set_net_config(db, net.id, "pat_transport_enabled", "true")
        set_net_config(db, net.id, "pat_http_base_url", "http://pat.test")
        db.commit()
    return app, settings


async def _nc(app, settings):
    from tests.conftest import make_test_token
    token = make_test_token("W0NC", settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"access_token": token})


async def test_connect_options_requires_nc(app_with_pat, monkeypatch):
    app, settings = app_with_pat
    monkeypatch.setattr("backend.modules.events.pat_routes.build_connect_options",
                        lambda client: {"aliases": [{"name": "gw1", "url": "telnet:///"}], "gateways": []})
    async with await _nc(app, settings) as c:
        r = await c.get(f"{BASE}/pat/connect-options")
    assert r.status_code == 200
    assert r.json()["aliases"][0]["name"] == "gw1"


async def test_connect_starts_session_and_second_is_409(app_with_pat, monkeypatch):
    app, settings = app_with_pat

    async def fake_start(*a, **k):
        return 7
    # first returns id, second raises busy
    calls = {"n": 0}

    async def start(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return 7
        raise pat_session.SessionBusy("busy")

    monkeypatch.setattr(pat_session.engine, "start", start)
    monkeypatch.setattr("backend.modules.events.pat_routes.resolve_connect_url",
                        lambda body, aliases: ("telnet:///", "alias: gw1"))
    monkeypatch.setattr("backend.modules.events.pat_routes.build_connect_options",
                        lambda client: {"aliases": [{"name": "gw1", "url": "telnet:///"}], "gateways": []})
    async with await _nc(app, settings) as c:
        r1 = await c.post(f"{BASE}/pat/connect", json={"alias": "gw1"})
        r2 = await c.post(f"{BASE}/pat/connect", json={"alias": "gw1"})
    assert r1.status_code == 201 and r1.json()["session_id"] == 7
    assert r2.status_code == 409


async def test_test_route_reports_ok(app_with_pat, monkeypatch):
    app, settings = app_with_pat
    monkeypatch.setattr("backend.modules.events.pat_routes._probe_status",
                        lambda client: True)
    async with await _nc(app, settings) as c:
        r = await c.post(f"{BASE}/pat/test")
    assert r.status_code == 200 and r.json()["ok"] is True
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_pat_routes.py -q` — FAIL.

- [ ] **Step 3: Implement `pat_routes.py`**

```python
# backend/modules/events/pat_routes.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.modules.nets.models import NetRole
from backend.integrations.winlink.models import PatConnectionSession
from backend.integrations.winlink.pat_client import PatClient, PatUnavailable
from backend.integrations.winlink.pat_config import (
    build_pat_client, pat_transport_enabled, resolve_pat_config,
)
from backend.integrations.winlink.pat_connect import build_connect_options, resolve_connect_url
from backend.integrations.winlink.pat_session import SessionBusy, engine

pat_router = APIRouter(prefix="/api/nets/{net_slug}", tags=["pat"])


class ConnectRequest(BaseModel):
    alias: str | None = None
    mode: str | None = None
    gateway: str | None = None
    freq: str | None = None


def _session_to_response(s: PatConnectionSession) -> dict:
    return {
        "id": s.id, "status": s.status.value, "method_label": s.method_label,
        "sent_count": s.sent_count, "received_count": s.received_count,
        "error": s.error, "events": s.events or [],
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
    }


def _require_enabled(db: Session, net_id: int) -> None:
    if not pat_transport_enabled(db, net_id):
        raise HTTPException(status_code=409, detail="PAT HTTP transport is not enabled for this net")


def _client(db: Session, net_id: int) -> PatClient:
    return build_pat_client(resolve_pat_config(db, net_id))


async def _do_connect(request: Request, db: Session, ctx: NetContext,
                      body: ConnectRequest, event_id: int | None):
    _require_enabled(db, ctx.net.id)
    client = _client(db, ctx.net.id)
    try:
        aliases = client.connect_aliases()
    except PatUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"PAT unreachable: {exc}")
    try:
        url, label = resolve_connect_url(body.model_dump(exclude_none=True), aliases)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    actor = ctx.user.callsign if ctx.user else ""
    # The engine's background task outlives this request, so it must open its own
    # sessions — hand it the app-level session_factory, never the request `db`.
    session_factory = request.app.state.session_factory
    try:
        session_id = await engine.start(
            session_factory, net_id=ctx.net.id, event_id=event_id,
            actor=actor, connect_url=url, method_label=label, client=client,
        )
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"session_id": session_id}


@pat_router.post("/events/{event_id}/pat/connect", status_code=201)
async def event_connect_route(event_id: int, body: ConnectRequest, request: Request,
                              ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                              db: Session = Depends(get_db_session)):
    return await _do_connect(request, db, ctx, body, event_id)


@pat_router.post("/pat/connect", status_code=201)
async def net_connect_route(body: ConnectRequest, request: Request,
                            ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                            db: Session = Depends(get_db_session)):
    return await _do_connect(request, db, ctx, body, None)


@pat_router.get("/pat/sessions/{session_id}")
async def session_status_route(session_id: int,
                               ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                               db: Session = Depends(get_db_session)):
    s = db.get(PatConnectionSession, session_id)
    if s is None or s.net_id != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(s)


@pat_router.post("/pat/sessions/{session_id}/abort")
async def session_abort_route(session_id: int, request: Request,
                              ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                              db: Session = Depends(get_db_session)):
    s = db.get(PatConnectionSession, session_id)
    if s is None or s.net_id != ctx.net.id:
        raise HTTPException(status_code=404, detail="Session not found")
    await engine.abort(request.app.state.session_factory, session_id, _client(db, ctx.net.id))
    return {"ok": True}


@pat_router.get("/pat/connect-options")
async def connect_options_route(ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                                db: Session = Depends(get_db_session)):
    _require_enabled(db, ctx.net.id)
    try:
        return build_connect_options(_client(db, ctx.net.id))
    except PatUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"PAT unreachable: {exc}")


def _probe_status(client: PatClient) -> bool:
    client.status()
    return True


@pat_router.post("/pat/test")
async def test_route(ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
                     db: Session = Depends(get_db_session)):
    cfg = resolve_pat_config(db, ctx.net.id)
    if not cfg.base_url:
        return {"ok": False, "error": "No PAT base URL configured"}
    try:
        _probe_status(build_pat_client(cfg))
        return {"ok": True}
    except PatUnavailable as exc:
        return {"ok": False, "error": str(exc)}
```
The routes take `request: Request` and pass `request.app.state.session_factory` (confirmed the handle `get_db_session` uses) into the engine, so the detached background task opens its own DB sessions.

- [ ] **Step 4: Register the router + lifespan shutdown in `app.py`**

Where routers are included, add:
```python
    from backend.modules.events.pat_routes import pat_router
    app.include_router(pat_router)
```
In the lifespan shutdown block (after the scanner task cancel), add:
```python
    from backend.integrations.winlink.pat_session import engine as pat_engine
    try:
        await pat_engine.shutdown()
    except Exception:
        pass
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_pat_routes.py -q` — PASS. Then `.venv/bin/pytest tests/test_event_message_routes.py -q` (no regression) and `nix-shell --run "ruff check"`.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/events/pat_routes.py backend/app.py tests/test_pat_routes.py
git commit -m "feat(pat): connect/session/options/test API routes + lifespan shutdown"
```

---

### Task 10: Frontend types + API + `usePatSession` hook

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/events.ts`
- Create: `frontend/src/hooks/usePatSession.ts`

**Interfaces:**
- Produces: types `PatConnectOptions`, `PatSession`, `PatSessionEvent`; API `fetchPatConnectOptions`, `patConnect`, `fetchPatSession`, `abortPatSession`, `testPatConnection`; hook `usePatSession(netSlug, sessionId | null)` polling session status until terminal.

- [ ] **Step 1: Add types**

Append to `frontend/src/types/index.ts`:
```typescript
export interface PatSessionEvent { ts: string; kind: string; text: string; }
export interface PatSession {
  id: number;
  status: "connecting" | "connected" | "syncing" | "completed" | "failed" | "aborted";
  method_label: string;
  sent_count: number;
  received_count: number;
  error: string | null;
  events: PatSessionEvent[];
  started_at: string | null;
  ended_at: string | null;
}
export interface PatConnectOptions {
  aliases: { name: string; url: string }[];
  gateways: { callsign: string; modes: string; freq: string }[];
}
export interface PatConnectInput {
  alias?: string;
  mode?: string;
  gateway?: string;
  freq?: string;
}
```

- [ ] **Step 2: Add API functions**

Append to `frontend/src/api/events.ts` (import the new types):
```typescript
export async function fetchPatConnectOptions(netSlug: string): Promise<PatConnectOptions> {
  return apiFetch<PatConnectOptions>(`/nets/${netSlug}/pat/connect-options`);
}

export async function patConnect(
  netSlug: string, eventId: number | null, input: PatConnectInput,
): Promise<{ session_id: number }> {
  const path = eventId == null
    ? `/nets/${netSlug}/pat/connect`
    : `/nets/${netSlug}/events/${eventId}/pat/connect`;
  return apiFetch<{ session_id: number }>(path, { method: "POST", body: JSON.stringify(input) });
}

export async function fetchPatSession(netSlug: string, sessionId: number): Promise<PatSession> {
  return apiFetch<PatSession>(`/nets/${netSlug}/pat/sessions/${sessionId}`);
}

export async function abortPatSession(netSlug: string, sessionId: number): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/nets/${netSlug}/pat/sessions/${sessionId}/abort`, { method: "POST" });
}

export async function testPatConnection(netSlug: string): Promise<{ ok: boolean; error?: string }> {
  return apiFetch<{ ok: boolean; error?: string }>(`/nets/${netSlug}/pat/test`, { method: "POST" });
}
```

- [ ] **Step 3: The polling hook**

```typescript
// frontend/src/hooks/usePatSession.ts
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchPatSession } from "../api/events";
import type { PatSession } from "../types";

const TERMINAL = new Set(["completed", "failed", "aborted"]);
const POLL_MS = 1500;

export function usePatSession(netSlug: string, sessionId: number | null) {
  const [session, setSession] = useState<PatSession | null>(null);
  const timer = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (timer.current !== null) { window.clearInterval(timer.current); timer.current = null; }
  }, []);

  useEffect(() => {
    setSession(null);
    if (sessionId == null) { stop(); return; }
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchPatSession(netSlug, sessionId);
        if (cancelled) return;
        setSession(s);
        if (TERMINAL.has(s.status)) stop();
      } catch { /* keep last-known on transient poll failure */ }
    };
    void tick();
    timer.current = window.setInterval(() => void tick(), POLL_MS);
    return () => { cancelled = true; stop(); };
  }, [netSlug, sessionId, stop]);

  return session;
}
```

- [ ] **Step 4: Build check + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean.
```bash
git add frontend/src/types/index.ts frontend/src/api/events.ts frontend/src/hooks/usePatSession.ts
git commit -m "feat(pat): frontend types, connect/session API, usePatSession hook"
```

---

### Task 11: Connect modal + live session panel (`PatConnectModal.tsx`)

**Files:**
- Create: `frontend/src/pages/events/PatConnectModal.tsx`

**Interfaces:**
- Consumes: `fetchPatConnectOptions`, `patConnect`, `abortPatSession` (Task 10 API); `usePatSession` (Task 10); `Modal`, `Button`, `Input`, `Spinner` (`frontend/src/components/`).
- Produces: `<PatConnectModal netSlug eventId open onClose onSettled />` — a modal that picks a connect (alias or advanced), starts a session, then shows the live session panel.

- [ ] **Step 1: Implement the component**

```tsx
// frontend/src/pages/events/PatConnectModal.tsx
import { useEffect, useState } from "react";
import { abortPatSession, fetchPatConnectOptions, patConnect } from "../../api/events";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { Spinner } from "../../components/Spinner";
import { usePatSession } from "../../hooks/usePatSession";
import type { PatConnectOptions } from "../../types";

interface Props {
  netSlug: string;
  eventId: number | null;
  open: boolean;
  onClose: () => void;
  onSettled: () => Promise<void>;
}

const TERMINAL = new Set(["completed", "failed", "aborted"]);

export function PatConnectModal({ netSlug, eventId, open, onClose, onSettled }: Props) {
  const [options, setOptions] = useState<PatConnectOptions | null>(null);
  const [alias, setAlias] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [mode, setMode] = useState("ardop");
  const [gateway, setGateway] = useState("");
  const [freq, setFreq] = useState("");
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const session = usePatSession(netSlug, sessionId);

  useEffect(() => {
    if (!open) return;
    setSessionId(null); setError(null); setAlias(""); setAdvanced(false);
    fetchPatConnectOptions(netSlug)
      .then((o) => { setOptions(o); if (o.aliases[0]) setAlias(o.aliases[0].name); })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load connect options"));
  }, [open, netSlug]);

  useEffect(() => {
    if (session && TERMINAL.has(session.status)) void onSettled();
  }, [session, onSettled]);

  async function start() {
    setError(null);
    try {
      const input = advanced ? { mode, gateway, freq: freq || undefined } : { alias };
      const { session_id } = await patConnect(netSlug, eventId, input);
      setSessionId(session_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connect failed");
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="PAT connection" size="lg">
      {error && <p className="text-danger text-sm mb-2">{error}</p>}
      {sessionId == null ? (
        !options ? <Spinner size="md" /> : (
          <div className="flex flex-col gap-3">
            {!advanced ? (
              <label className="text-sm flex flex-col gap-1">
                Connect alias
                <select className="border border-border rounded p-1 bg-bg-elevated"
                  value={alias} onChange={(e) => setAlias(e.target.value)}>
                  {options.aliases.length === 0 && <option value="">(no aliases in PAT)</option>}
                  {options.aliases.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
                </select>
              </label>
            ) : (
              <div className="flex flex-col gap-2">
                <label className="text-sm flex flex-col gap-1">Mode
                  <select className="border border-border rounded p-1 bg-bg-elevated"
                    value={mode} onChange={(e) => setMode(e.target.value)}>
                    {["telnet", "ardop", "vara", "varafm", "packet", "pactor"].map((m) =>
                      <option key={m} value={m}>{m}</option>)}
                  </select>
                </label>
                <Input label="Gateway callsign" value={gateway}
                  onChange={(e) => setGateway(e.target.value)} placeholder="KE0GW"
                  list="pat-gateways" />
                <datalist id="pat-gateways">
                  {options.gateways.map((g) => <option key={g.callsign} value={g.callsign}>{g.modes} {g.freq}</option>)}
                </datalist>
                <Input label="Frequency (optional)" value={freq}
                  onChange={(e) => setFreq(e.target.value)} placeholder="7100" />
              </div>
            )}
            <button className="text-xs text-accent text-left" onClick={() => setAdvanced((v) => !v)}>
              {advanced ? "← Use a saved alias" : "Advanced: build a connect →"}
            </button>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button onClick={() => void start()}
                disabled={advanced ? !gateway : !alias}>Connect</Button>
            </div>
          </div>
        )
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 rounded text-xs bg-bg-elevated">{session?.status ?? "connecting"}</span>
            <span className="text-sm text-text-muted">{session?.method_label}</span>
          </div>
          <div className="text-xs text-text-secondary">
            sent {session?.sent_count ?? 0} · received {session?.received_count ?? 0}
          </div>
          <div className="max-h-64 overflow-y-auto bg-bg-elevated rounded p-2 font-mono text-xs">
            {(session?.events ?? []).map((e, i) => <div key={i}>{e.text}</div>)}
            {session && session.status === "connecting" && <Spinner size="sm" />}
          </div>
          {session?.error && <p className="text-danger text-sm">{session.error}</p>}
          <div className="flex justify-end gap-2">
            {session && !TERMINAL.has(session.status) && (
              <Button variant="secondary" onClick={() => void abortPatSession(netSlug, sessionId)}>Abort</Button>
            )}
            {session && TERMINAL.has(session.status) && <Button onClick={onClose}>Done</Button>}
          </div>
        </div>
      )}
    </Modal>
  );
}
```

- [ ] **Step 2: Build check + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean. (If `Modal` has no `size="lg"` or `Input` lacks `list`, adapt to the real component API and note it.)
```bash
git add frontend/src/pages/events/PatConnectModal.tsx
git commit -m "feat(pat): connect modal with alias/advanced picker and live session panel"
```

---

### Task 12: MessagesPanel wiring + message status badges

**Files:**
- Modify: `frontend/src/pages/events/MessagesPanel.tsx`
- Modify: `frontend/src/types/index.ts` (extend `MessageStatus` if needed for `queued`)

**Interfaces:**
- Consumes: `PatConnectModal` (Task 11); the existing `MessagesPanel` guards (`canWrite && active`) and `onChanged`.

- [ ] **Step 1: Show delivery status on outbound rows**

Outbound message rows currently show `→ {to_address}`. Add a delivery badge derived from the message's delivery state. If the `EventMessage` response does not already expose winlink delivery status, surface it: in `_message_extras`/`_message_to_response` (`backend/modules/events/routes.py`) include `delivery_status` for outbound messages by reading the `winlink` `DeliveryLog` row for `("event_message", id)` (values: `pending`/`queued`/`sent`/`failed`), and add `delivery_status?: string` to the `EventMessage` type. Render:
```tsx
{m.direction === "outbound" && m.delivery_status && (
  <span className="text-xs" title={m.delivery_status}>
    {m.delivery_status === "queued" ? "📤" : m.delivery_status === "sent" ? "✓" : m.delivery_status === "failed" ? "⚠️" : "…"}
  </span>
)}
```
(Write a backend test in `tests/test_event_message_routes.py` asserting an outbound event message with a `QUEUED` winlink `DeliveryLog` returns `delivery_status: "queued"`.)

- [ ] **Step 2: Add the Connect button + modal**

In the panel header actions (inside the existing `canWrite && active` guard), add:
```tsx
const [patOpen, setPatOpen] = useState(false);
// ...
<Button size="sm" variant="secondary" onClick={() => setPatOpen(true)}>Connect (PAT)</Button>
// ...at the end of the panel JSX:
<PatConnectModal netSlug={netSlug} eventId={event.id} open={patOpen}
  onClose={() => setPatOpen(false)} onSettled={onChanged} />
```
`onChanged` (the existing refresh) repaints message rows so `queued → sent` and newly-received inbound messages appear after a session settles.

- [ ] **Step 3: Build + backend test + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean.
Run: `.venv/bin/pytest tests/test_event_message_routes.py -q` — PASS (incl. the new delivery_status test).
```bash
git add frontend/src/pages/events/MessagesPanel.tsx frontend/src/types/index.ts backend/modules/events/routes.py tests/test_event_message_routes.py
git commit -m "feat(pat): Connect button + outbound delivery-status badges in Messages panel"
```

---

### Task 13: Net admin config UI — PAT HTTP fields + Test button

**Files:**
- Modify: the net admin config page (find it: `frontend/src/pages/**/NetConfig*.tsx` or the settings panel that renders `delivery.*`/`scanner.*` fields) and its API module.
- Modify: `backend/modules/nets/routes.py` if a dedicated write path is needed for the PAT secrets (mirror the SMTP-password write-only pattern).

**Interfaces:**
- Consumes: `testPatConnection` (Task 10); the existing net-config GET/PUT routes + secret write pattern.

- [ ] **Step 1: Config fields**

In the net config UI, add a "PAT transport" section with inputs bound to the config keys: `pat_transport_enabled` (checkbox), `pat_http_base_url`, `pat_http_auth_mode` (select none/basic/token), `pat_http_username`, `pat_http_password` (password field, write-only — blank means "unchanged"), `pat_http_token` (password field, write-only), `pat_http_timeout_seconds`. Save via the existing bulk-config PUT. Ensure the two secret fields are encrypted on the backend write path (they contain `password`/`token`, which `SENSITIVE_KEY_FRAGMENTS` already matches, so `set_config`/bulk-config encrypts them — verify the net-config bulk write applies `secret_box.encrypt` for sensitive keys; if not, extend it to match the AppConfig sensitive-key handling).

- [ ] **Step 2: Test button**

Add a "Test connection" button next to the base URL that calls `testPatConnection(netSlug)` and shows green ("Reached PAT") / red (the `error`), mirroring the groups.io test button UX.

- [ ] **Step 3: Build + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean.
Run: `.venv/bin/pytest tests/ -q -k "config or net"` — PASS.
```bash
git add frontend/src backend/modules/nets/routes.py tests/
git commit -m "feat(pat): net-admin PAT HTTP transport config + test-connection button"
```

---

### Task 14: Final verification sweep

- [ ] **Step 1: Full backend suite + lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — all pass.

- [ ] **Step 2: Frontend build**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean.

- [ ] **Step 3: Migration chain on a scratch DB**

Run: `SKYNET_DATABASE_URL="sqlite:////tmp/claude-sp5-final.db" .venv/bin/alembic upgrade head && SKYNET_DATABASE_URL="sqlite:////tmp/claude-sp5-final.db" .venv/bin/alembic downgrade -1 && rm -f /tmp/claude-sp5-final.db` — clean up and down through `a1b2c3d4e5f6`.

- [ ] **Step 4: Fallback regression check**

Confirm that with `pat_transport_enabled` unset (default), outbound still writes a `.b2f` file and the scanner still reads the mailbox dir — i.e. all pre-existing winlink/scanner tests pass unchanged (they carry no `pat_http` config). This is the safety-valve guarantee.

- [ ] **Step 5: Manual smoke test (human checkpoint)**

With a reachable PAT HTTP endpoint configured (base URL + optional auth) and `pat_transport_enabled=true`: activate an event → Messages panel → compose an outbound (📤 QUEUED) → **Connect (PAT)** → pick an alias (or Advanced: mode+gateway+freq) → watch the live session log + counts → on completion the outbound flips to ✓ SENT and any received traffic imports into the panel. Verify **Test connection** in net admin (green/red). Verify a viewer sees no Connect control. **This is also the moment to close the SP4a/SP4b interop pinning gate**: send a composed form through the real round-trip and confirm it renders as a form in RMS Express / PAT; capture that real sample as a pinned fixture.

---

## Notes for the implementer

- **`session_factory`, not request `db`, into the engine.** The connect route's background task outlives the HTTP request; pass `request.app.state.session_factory` to `engine.start`, never the request-scoped session. The `_factory(db)` helper in Task 9 is a placeholder — replace it with `request.app.state.session_factory` (add `request: Request` to the routes).
- **Reconciliation is station-global.** A connect sends PAT's whole outbox, so `_reconcile_outbound` scans all `winlink` `QUEUED` rows, not just the triggering net's. That is intended.
- **`stream_status` is the only websocket surface.** Keep `websockets` usage inside `pat_client.stream_status`; the engine and tests only see an async iterator, so no test needs a live socket.
- **Enum migration:** verify the `delivery_logs.status` CHECK-constraint behavior empirically in Task 3 Step 6 — if SQLite rejects `"queued"`, extend the batch block to rebuild the column; if the column is a bare VARCHAR, the two `add_column`s suffice.
