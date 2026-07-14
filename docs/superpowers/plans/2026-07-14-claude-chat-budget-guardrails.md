# Claude Chat Budget Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict the Activities brainstorm chat to amateur-radio net topics and bound its Anthropic API spend with admin-tunable daily caps and a history window.

**Architecture:** All enforcement lives in the backend. `chat_service.py` gets a topic-guard system prompt, a current model default, a 40-message history window, and per-message user attribution (`sender_callsign` column). The send-message route checks two AppConfig-backed daily limits (per-user and global, UTC day) before any API spend and returns 429 with a friendly detail — which the existing frontend already surfaces via `e?.detail` toasts. The admin Config page grows two fields.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest (backend); React/TypeScript (frontend, config fields only).

**Spec:** `docs/superpowers/specs/2026-07-14-claude-chat-budget-guardrails-design.md`

## Global Constraints

- Toolchain: host is NixOS — backend tests via `.venv/bin/pytest -q`, lint via `nix-shell --run "ruff check"`, frontend via `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`.
- Ruff: line-length 120, `select = ["E", "F"]`. Production code has no permissive ignores.
- Commits: Conventional Commits.
- Config keys: `claude_daily_user_message_limit` (default `25`), `claude_daily_global_message_limit` (default `100`), `0` = unlimited. Neither key contains a sensitive fragment (`api_key`/`password`/`secret`/`token`), so no encryption handling is needed.
- Model default becomes `claude-sonnet-4-6` (replaces deprecated `claude-sonnet-4-20250514`). `max_tokens` stays 1024.
- History window: last **40** messages sent to the API; full history stays in DB/UI.
- Day boundary for caps: **UTC**.

---

### Task 1: Topic-guard system prompt, current model, history window

**Files:**
- Modify: `backend/modules/activities/chat_service.py`
- Test: `tests/test_chat_service.py` (new file)

**Interfaces:**
- Consumes: existing `ChatSession`/`ChatMessage` models, `_call_claude(api_key, messages, model)`.
- Produces: module constants `DEFAULT_MODEL = "claude-sonnet-4-6"` and `HISTORY_WINDOW = 40`; `send_message()` sends at most `HISTORY_WINDOW` messages to the API. Signature unchanged in this task.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chat_service.py`:

```python
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.activities import chat_service
from backend.modules.activities.chat_service import (
    HISTORY_WINDOW,
    SYSTEM_PROMPT,
    create_chat_session,
    send_message,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def fake_claude(monkeypatch):
    """Replace _call_claude; capture the messages payload it receives."""
    calls = []

    def _fake(api_key, messages, model=chat_service.DEFAULT_MODEL):
        calls.append({"api_key": api_key, "messages": messages, "model": model})
        return SimpleNamespace(content=[SimpleNamespace(text="A fun activity idea")])

    monkeypatch.setattr(chat_service, "_call_claude", _fake)
    return calls


def test_default_model_is_current_sonnet():
    assert chat_service.DEFAULT_MODEL == "claude-sonnet-4-6"


def test_system_prompt_scopes_to_amateur_radio_and_declines_off_topic():
    lowered = SYSTEM_PROMPT.lower()
    assert "amateur radio" in lowered
    assert "decline" in lowered
    # Multi-mode scope: not just Winlink.
    for mode in ("winlink", "cw", "packet"):
        assert mode in lowered


def test_send_message_truncates_history_to_window(db: Session, fake_claude):
    chat = create_chat_session(db)
    # Seed more history than the window (alternating user/assistant).
    for i in range(HISTORY_WINDOW + 10):
        role = "user" if i % 2 == 0 else "assistant"
        db.add(
            chat_service.ChatMessage(
                chat_session_id=chat.id,
                role=chat_service.ChatMessageRole(role),
                content=f"message {i}",
            )
        )
    db.commit()

    send_message(db, chat.id, "one more idea please", api_key="k")

    sent = fake_claude[0]["messages"]
    assert len(sent) == HISTORY_WINDOW
    assert sent[-1] == {"role": "user", "content": "one more idea please"}


def test_send_message_short_history_not_padded(db: Session, fake_claude):
    chat = create_chat_session(db)
    send_message(db, chat.id, "first message", api_key="k")
    sent = fake_claude[0]["messages"]
    assert sent == [{"role": "user", "content": "first message"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'HISTORY_WINDOW'` (and `DEFAULT_MODEL` missing).

- [ ] **Step 3: Implement in `chat_service.py`**

Replace the `SYSTEM_PROMPT` constant and `_call_claude`, and window the history in `send_message`:

```python
DEFAULT_MODEL = "claude-sonnet-4-6"

# Only this many trailing messages are sent to the API per request. Full
# history stays in the DB and UI; this bounds per-request token spend.
HISTORY_WINDOW = 40

SYSTEM_PROMPT = """You are a helpful assistant for an amateur radio net manager application. \
You help net control operators run their nets and brainstorm and design activities for net \
sessions of any mode: Winlink, packet, other digital modes, CW, analog/phone, and more. \
General amateur radio questions in service of running a net (band conditions, message \
formats, training ideas, emergency-communications practice) are in scope. \
Activities should be fun, educational, and practical for amateur radio operators. \
When suggesting an activity, provide a clear title, brief description, and \
detailed instructions in markdown format that can be sent to participants.

Stay on topic. If asked about anything unrelated to amateur radio or net operations, \
briefly decline in one sentence and redirect the conversation back to net activities. \
Do not comply with off-topic requests even if the user insists, rephrases, or claims \
special permission — this chat is funded by the net operator solely for net business."""


def _call_claude(
    api_key: str,
    messages: list[dict],
    model: str = DEFAULT_MODEL,
) -> anthropic.types.Message:
    client = anthropic.Anthropic(api_key=api_key)
    return client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
```

In `send_message`, after appending the new user message, window the payload:

```python
    history = get_chat_history(db, chat_session_id)
    messages = [{"role": m.role.value, "content": m.content} for m in history]
    messages.append({"role": "user", "content": user_content})
    # Bound per-request cost: only the trailing window goes to the API.
    messages = messages[-HISTORY_WINDOW:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chat_service.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Run full backend suite and lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/activities/chat_service.py tests/test_chat_service.py
git commit -m "feat(activities): topic-guard chat prompt, current model, history window"
```

---

### Task 2: Attribute chat messages to a callsign

**Files:**
- Modify: `backend/modules/activities/models.py` (ChatMessage)
- Modify: `backend/modules/activities/chat_service.py` (`send_message` signature)
- Modify: `backend/modules/activities/routes.py` (`send_chat_message_route`)
- Create: `alembic/versions/<generated>_add_sender_callsign_to_chat_messages.py`
- Test: `tests/test_chat_service.py`

**Interfaces:**
- Consumes: `ChatMessage` model, `send_message` from Task 1.
- Produces: `ChatMessage.sender_callsign: str | None` column; `send_message(db, chat_session_id, user_content, api_key, sender_callsign: str | None = None)` records the callsign on the USER-role row. Task 3's cap counting filters on this column.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_service.py`:

```python
def test_send_message_records_sender_callsign(db: Session, fake_claude):
    chat = create_chat_session(db)
    user_msg, assistant_msg = send_message(
        db, chat.id, "brainstorm a packet night", api_key="k", sender_callsign="W0NC"
    )
    assert user_msg.sender_callsign == "W0NC"
    assert assistant_msg.sender_callsign is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chat_service.py::test_send_message_records_sender_callsign -v`
Expected: FAIL — `send_message() got an unexpected keyword argument 'sender_callsign'`.

- [ ] **Step 3: Add the column to the model**

In `backend/modules/activities/models.py`, add to `ChatMessage` after `content`:

```python
    # Callsign of the operator who sent a USER-role message; NULL for
    # assistant messages and rows created before attribution existed.
    sender_callsign: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

(`String` is already imported in this file.)

- [ ] **Step 4: Thread the callsign through `send_message`**

In `chat_service.py`:

```python
def send_message(
    db: Session,
    chat_session_id: int,
    user_content: str,
    api_key: str,
    sender_callsign: str | None = None,
) -> tuple[ChatMessage, ChatMessage]:
```

and set it on the user message:

```python
    user_msg = ChatMessage(
        chat_session_id=chat_session_id,
        role=ChatMessageRole.USER,
        content=user_content,
        sender_callsign=sender_callsign,
    )
```

In `routes.py` `send_chat_message_route`, pass the caller's callsign (under `require_net_role`, `ctx.user` is always set):

```python
        user_msg, assistant_msg = send_message(
            db, chat_session_id, body.content, api_key=api_key, sender_callsign=ctx.user.callsign
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chat_service.py -v`
Expected: 5 PASS.

- [ ] **Step 6: Create the Alembic migration**

Run: `.venv/bin/alembic revision -m "add sender_callsign to chat_messages"`
(The generated file auto-chains `down_revision` to the current head, `9a1f8e3c40b2`.)

Fill in the generated file's `upgrade`/`downgrade`:

```python
def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("sender_callsign", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "sender_callsign")
```

- [ ] **Step 7: Verify the migration runs**

Run against a scratch database:

```bash
cd /home/ku0hn/dev/SkyNetControl
SKYNET_DATABASE_URL="sqlite:///$(mktemp -d)/mig.db" .venv/bin/alembic upgrade head
```

Expected: completes without error, ending at the new revision.

- [ ] **Step 8: Run full backend suite and lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add backend/modules/activities/models.py backend/modules/activities/chat_service.py \
    backend/modules/activities/routes.py alembic/versions/*sender_callsign*
git commit -m "feat(activities): attribute chat messages to sender callsign"
```

---

### Task 3: Daily usage caps (per-user + global)

**Files:**
- Modify: `backend/modules/activities/chat_service.py` (add `count_user_messages_today`)
- Modify: `backend/modules/activities/routes.py` (cap enforcement in `send_chat_message_route`)
- Test: `tests/test_chat_service.py`, `tests/test_activity_routes.py`

**Interfaces:**
- Consumes: `ChatMessage.sender_callsign` (Task 2), `get_config_value(db, key, default)` from `backend.config_mgmt.service`, `AppConfig` from `backend.config_mgmt.models`.
- Produces: `count_user_messages_today(db, sender_callsign: str | None = None) -> int` (None = count all users); route returns **429** before any Anthropic call when a nonzero limit is reached. Config keys `claude_daily_user_message_limit` / `claude_daily_global_message_limit` with in-code defaults `"25"` / `"100"`.

- [ ] **Step 1: Write the failing service-level test**

Append to `tests/test_chat_service.py`:

```python
def test_count_user_messages_today(db: Session, fake_claude):
    chat = create_chat_session(db)
    send_message(db, chat.id, "idea one", api_key="k", sender_callsign="W0NC")
    send_message(db, chat.id, "idea two", api_key="k", sender_callsign="W0NC")
    send_message(db, chat.id, "idea three", api_key="k", sender_callsign="W0NE")
    # Legacy row without attribution counts globally but not per-user.
    db.add(
        chat_service.ChatMessage(
            chat_session_id=chat.id,
            role=chat_service.ChatMessageRole.USER,
            content="legacy",
        )
    )
    db.commit()

    assert chat_service.count_user_messages_today(db, "W0NC") == 2
    assert chat_service.count_user_messages_today(db, "W0NE") == 1
    # Global count: 4 user messages; assistant replies are not counted.
    assert chat_service.count_user_messages_today(db) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chat_service.py::test_count_user_messages_today -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'count_user_messages_today'`.

- [ ] **Step 3: Implement the counter in `chat_service.py`**

Add `from sqlalchemy import func` to the imports, then:

```python
def count_user_messages_today(db: Session, sender_callsign: str | None = None) -> int:
    """Count USER-role chat messages created since midnight UTC.

    With *sender_callsign*, counts that operator's messages; without it,
    counts all user messages (the global-cap denominator, which includes
    legacy NULL-callsign rows).
    """
    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    query = (
        db.query(func.count(ChatMessage.id))
        .filter(ChatMessage.role == ChatMessageRole.USER)
        .filter(ChatMessage.created_at >= start_of_day)
    )
    if sender_callsign is not None:
        query = query.filter(ChatMessage.sender_callsign == sender_callsign)
    return query.scalar() or 0
```

(Add `from datetime import datetime, timezone` if not already imported in this module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_chat_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing route tests**

Append to `tests/test_activity_routes.py`. Add imports at the top of the file:

```python
from backend.config_mgmt.models import AppConfig
from backend.modules.activities.models import ChatMessage, ChatMessageRole
```

Then the tests (note: `routes.py` imports `send_message` by name, so monkeypatch it on the *routes* module):

```python
def _seed_chat_and_config(db_setup, *, user_limit, global_limit, seed_messages=()):
    """Create a chat session, config rows, and pre-existing user messages.

    seed_messages is an iterable of callsign-or-None values; one USER-role
    message is created per entry (created_at defaults to now, i.e. today).
    """
    factory = db_setup["factory"]
    with factory() as session:
        chat = ChatSession()
        session.add(chat)
        session.add(AppConfig(key="claude_api_key", value="test-key"))
        session.add(AppConfig(key="claude_daily_user_message_limit", value=str(user_limit)))
        session.add(AppConfig(key="claude_daily_global_message_limit", value=str(global_limit)))
        session.flush()
        for callsign in seed_messages:
            session.add(
                ChatMessage(
                    chat_session_id=chat.id,
                    role=ChatMessageRole.USER,
                    content="seeded",
                    sender_callsign=callsign,
                )
            )
        session.commit()
        return chat.id


@pytest.fixture
def fake_send_message(monkeypatch):
    """Replace routes.send_message; records calls, persists real rows."""
    calls = []

    def _fake(db, chat_session_id, user_content, api_key, sender_callsign=None):
        calls.append(user_content)
        user_msg = ChatMessage(
            chat_session_id=chat_session_id,
            role=ChatMessageRole.USER,
            content=user_content,
            sender_callsign=sender_callsign,
        )
        assistant_msg = ChatMessage(
            chat_session_id=chat_session_id,
            role=ChatMessageRole.ASSISTANT,
            content="stub reply",
        )
        db.add_all([user_msg, assistant_msg])
        db.commit()
        db.refresh(user_msg)
        db.refresh(assistant_msg)
        return user_msg, assistant_msg

    import backend.modules.activities.routes as activities_routes

    monkeypatch.setattr(activities_routes, "send_message", _fake)
    return calls


@pytest.mark.asyncio
async def test_chat_per_user_cap_returns_429(nc_client, db_setup, fake_send_message):
    chat_id = _seed_chat_and_config(
        db_setup, user_limit=2, global_limit=0, seed_messages=["W0NC", "W0NC"]
    )
    resp = await nc_client.post(f"{BASE}/chat/sessions/{chat_id}/messages", json={"content": "hi"})
    assert resp.status_code == 429
    assert "limit" in resp.json()["detail"].lower()
    assert fake_send_message == []  # capped before any API call


@pytest.mark.asyncio
async def test_chat_per_user_cap_is_per_callsign(admin_client, db_setup, fake_send_message):
    # W0NC is at the limit, but W0NE (admin) is not.
    chat_id = _seed_chat_and_config(
        db_setup, user_limit=2, global_limit=0, seed_messages=["W0NC", "W0NC"]
    )
    resp = await admin_client.post(f"{BASE}/chat/sessions/{chat_id}/messages", json={"content": "hi"})
    assert resp.status_code == 200
    assert fake_send_message == ["hi"]


@pytest.mark.asyncio
async def test_chat_global_cap_counts_legacy_rows(nc_client, db_setup, fake_send_message):
    # Global cap of 2 met by one attributed and one legacy NULL-callsign row.
    chat_id = _seed_chat_and_config(
        db_setup, user_limit=0, global_limit=2, seed_messages=["W0NE", None]
    )
    resp = await nc_client.post(f"{BASE}/chat/sessions/{chat_id}/messages", json={"content": "hi"})
    assert resp.status_code == 429
    assert fake_send_message == []


@pytest.mark.asyncio
async def test_chat_zero_limits_disable_caps(nc_client, db_setup, fake_send_message):
    chat_id = _seed_chat_and_config(
        db_setup, user_limit=0, global_limit=0, seed_messages=["W0NC"] * 50
    )
    resp = await nc_client.post(f"{BASE}/chat/sessions/{chat_id}/messages", json={"content": "hi"})
    assert resp.status_code == 200
    assert fake_send_message == ["hi"]
```

- [ ] **Step 6: Run route tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity_routes.py -k "cap" -v`
Expected: the three cap tests FAIL (200 instead of 429); `test_chat_zero_limits_disable_caps` may already pass.

- [ ] **Step 7: Enforce caps in the route**

In `routes.py`, add `count_user_messages_today` to the `chat_service` import block, then add a module-level helper near `_message_to_response`:

```python
def _config_int(db: Session, key: str, default: str) -> int:
    raw = get_config_value(db, key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)
```

In `send_chat_message_route`, after the 404 chat lookup and **before** the `claude_api_key` check:

```python
    # Budget guardrails: cheap DB checks before any API spend.
    user_limit = _config_int(db, "claude_daily_user_message_limit", "25")
    if user_limit > 0 and count_user_messages_today(db, ctx.user.callsign) >= user_limit:
        raise HTTPException(
            status_code=429,
            detail="Daily chat limit reached for your callsign — resets at midnight UTC.",
        )
    global_limit = _config_int(db, "claude_daily_global_message_limit", "100")
    if global_limit > 0 and count_user_messages_today(db) >= global_limit:
        raise HTTPException(
            status_code=429,
            detail="Daily chat limit for this app has been reached — resets at midnight UTC.",
        )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity_routes.py tests/test_chat_service.py -v`
Expected: all PASS.

- [ ] **Step 9: Run full backend suite and lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add backend/modules/activities/chat_service.py backend/modules/activities/routes.py \
    tests/test_chat_service.py tests/test_activity_routes.py
git commit -m "feat(activities): enforce daily per-user and global chat caps"
```

---

### Task 4: Admin UI fields for the two limits

**Files:**
- Modify: `frontend/src/pages/ConfigPage.tsx` (INTEGRATIONS_FIELDS)

**Interfaces:**
- Consumes: existing `ConfigField` interface (`frontend/src/components/SettingsSection.tsx` — `type?: "text" | "boolean" | "multiselect"`, default text) and the generic config PUT route (accepts arbitrary keys; these keys are non-sensitive so no encryption path).
- Produces: two editable text fields whose keys match Task 3's config keys exactly.

Note: no other frontend change is needed — `apiFetch` already extracts `body.detail` from non-OK responses into `ApiError`, and `BrainstormPanel.tsx` already toasts `e?.detail`, so the 429 message surfaces as-is.

- [ ] **Step 1: Add the fields**

In `frontend/src/pages/ConfigPage.tsx`, extend `INTEGRATIONS_FIELDS` after the `claude_api_key` entry:

```tsx
  {
    key: "claude_daily_user_message_limit",
    label: "Claude Daily Per-User Message Limit",
    placeholder: "25",
    helpText:
      "Max brainstorm-chat messages each operator may send per UTC day. 0 = unlimited. Default 25.",
  },
  {
    key: "claude_daily_global_message_limit",
    label: "Claude Daily Global Message Limit",
    placeholder: "100",
    helpText:
      "Max brainstorm-chat messages across all operators per UTC day. 0 = unlimited. Default 100.",
  },
```

- [ ] **Step 2: Verify the frontend builds**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ConfigPage.tsx
git commit -m "feat(config): admin fields for Claude chat daily limits"
```

---

### Task 5: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 2: Lint**

Run: `nix-shell --run "ruff check"`
Expected: clean.

- [ ] **Step 3: Frontend production build**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: success.
