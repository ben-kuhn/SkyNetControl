# Configurable Check-in Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the list of recognized check-in modes configurable via `AppConfig`, use it in the message parser, and expose it to the frontend for the Add Check-in dropdown.

**Architecture:** A new helper function `get_checkin_modes(db)` in the config service returns the configured modes list (or sensible defaults). The message parser gains an optional `known_modes` parameter. A new `GET /api/checkins/modes` endpoint exposes the list. The frontend fetches modes on page load and uses them in the Add Check-in dropdown.

**Tech Stack:** FastAPI, SQLAlchemy, Python, React/TypeScript

---

## File Structure

**Modified files:**

| File | Change |
|------|--------|
| `backend/config_mgmt/service.py` | Add `get_checkin_modes(db)` helper |
| `backend/modules/checkins/message_parser.py` | Accept optional `known_modes` param in `parse_plain_text_message` |
| `backend/modules/checkins/service.py` | Pass configured modes to parser |
| `backend/modules/checkins/routes.py` | Add `GET /modes` endpoint |
| `frontend/src/api/checkins.ts` | Add `fetchModes()` function |
| `frontend/src/pages/CheckInsPage.tsx` | Dynamic mode dropdown in AddCheckinModal |
| `tests/test_message_parser.py` | Test custom modes in parser |
| `tests/test_checkin_routes.py` | Test modes endpoint |

---

### Task 1: Add `get_checkin_modes` to config service

**Files:**
- Modify: `backend/config_mgmt/service.py`
- Create: `tests/test_checkin_modes.py`

- [x] **Step 1: Write the failing test**

Create `tests/test_checkin_modes.py`:

```python
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.service import get_checkin_modes


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


def test_get_checkin_modes_default(db):
    """Returns default modes when no config is set."""
    modes = get_checkin_modes(db)
    assert "Voice" in modes
    assert "Winlink" in modes
    assert "VARA FM" in modes
    assert len(modes) == 12


def test_get_checkin_modes_custom(db):
    """Returns custom modes when config is set."""
    custom = ["Voice", "Winlink", "Custom Mode"]
    config = AppConfig(key="checkins.modes", value=json.dumps(custom))
    db.add(config)
    db.commit()

    modes = get_checkin_modes(db)
    assert modes == custom


def test_get_checkin_modes_invalid_json(db):
    """Returns defaults on invalid JSON."""
    config = AppConfig(key="checkins.modes", value="not json")
    db.add(config)
    db.commit()

    modes = get_checkin_modes(db)
    assert len(modes) == 12
```

- [x] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_checkin_modes.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with `ImportError: cannot import name 'get_checkin_modes'`

- [x] **Step 3: Implement `get_checkin_modes`**

Add to `backend/config_mgmt/service.py`:

```python
import json

DEFAULT_CHECKIN_MODES = [
    "Voice", "Winlink", "VARA FM", "VARA HF", "ARDOP",
    "1200-baud Packet", "9k6 Packet", "Pactor", "Telnet",
    "AX.25", "CW", "Digital",
]


def get_checkin_modes(db: Session) -> list[str]:
    raw = get_config_value(db, "checkins.modes")
    if raw is None:
        return DEFAULT_CHECKIN_MODES
    try:
        modes = json.loads(raw)
        if isinstance(modes, list) and all(isinstance(m, str) for m in modes):
            return modes
    except (json.JSONDecodeError, TypeError):
        pass
    return DEFAULT_CHECKIN_MODES
```

- [x] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_checkin_modes.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: PASS (all 3 tests)

- [x] **Step 5: Commit**

```bash
git add backend/config_mgmt/service.py tests/test_checkin_modes.py
git commit -m "feat: add get_checkin_modes config helper with defaults"
```

---

### Task 2: Make message parser accept configurable modes

**Files:**
- Modify: `backend/modules/checkins/message_parser.py:114`
- Modify: `backend/modules/checkins/service.py`
- Test: `tests/test_message_parser.py`

- [x] **Step 1: Write the failing test**

Add to `tests/test_message_parser.py`:

```python
def test_parse_plain_text_custom_modes():
    """Parser uses custom known_modes when provided."""
    body = "John Smith W0ABC Denver CO VARA-FM Running well"
    result = parse_plain_text_message(body, known_modes={"vara-fm"})
    assert result["mode"] == "VARA-FM"
    assert result["comments"] == "Running well"


def test_parse_plain_text_default_modes_still_work():
    """Without known_modes param, defaults still work."""
    body = "John Smith W0ABC Denver CO Winlink"
    result = parse_plain_text_message(body)
    assert result["mode"] == "Winlink"
```

- [x] **Step 2: Run tests to verify first test fails**

Run: `nix-shell --run "python -m pytest tests/test_message_parser.py::test_parse_plain_text_custom_modes -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL (TypeError — unexpected keyword argument `known_modes`)

- [x] **Step 3: Update `parse_plain_text_message` to accept optional `known_modes`**

In `backend/modules/checkins/message_parser.py`, change the function signature and the hardcoded set:

Replace:
```python
def parse_plain_text_message(body: str) -> dict:
```
With:
```python
DEFAULT_KNOWN_MODES = {"winlink", "vara", "ardop", "packet", "pactor", "telnet", "ax.25"}


def parse_plain_text_message(body: str, known_modes: set[str] | None = None) -> dict:
```

And replace line 114:
```python
    known_modes = {"winlink", "vara", "ardop", "packet", "pactor", "telnet", "ax.25"}
```
With:
```python
    if known_modes is None:
        known_modes = DEFAULT_KNOWN_MODES
```

Also update `parse_message` to pass through:

Replace:
```python
def parse_message(body: str) -> tuple[MessageType, dict]:
    """Detect message type and parse accordingly."""
    msg_type = detect_message_type(body)

    if msg_type == MessageType.FORM:
        return msg_type, parse_form_message(body)
    elif msg_type == MessageType.PLAIN_TEXT:
        return msg_type, parse_plain_text_message(body)
```
With:
```python
def parse_message(body: str, known_modes: set[str] | None = None) -> tuple[MessageType, dict]:
    """Detect message type and parse accordingly."""
    msg_type = detect_message_type(body)

    if msg_type == MessageType.FORM:
        return msg_type, parse_form_message(body)
    elif msg_type == MessageType.PLAIN_TEXT:
        return msg_type, parse_plain_text_message(body, known_modes=known_modes)
```

- [x] **Step 4: Update service.py to pass configured modes to the parser**

In `backend/modules/checkins/service.py`, in `process_raw_message`:

Add import at the top:
```python
from backend.config_mgmt.service import get_checkin_modes
```

Replace:
```python
    msg_type, fields = parse_message(raw.body)
```
With:
```python
    configured_modes = get_checkin_modes(db)
    # Build a lowercase set for case-insensitive matching in the parser
    modes_set = {m.lower() for m in configured_modes}
    msg_type, fields = parse_message(raw.body, known_modes=modes_set)
```

- [x] **Step 5: Run all parser and checkin tests**

Run: `nix-shell --run "python -m pytest tests/test_message_parser.py tests/test_checkin_routes.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass

- [x] **Step 6: Commit**

```bash
git add backend/modules/checkins/message_parser.py backend/modules/checkins/service.py tests/test_message_parser.py
git commit -m "feat: make message parser known_modes configurable"
```

---

### Task 3: Add `GET /api/checkins/modes` endpoint

**Files:**
- Modify: `backend/modules/checkins/routes.py`
- Test: `tests/test_checkin_routes.py`

- [x] **Step 1: Write the failing test**

Add to `tests/test_checkin_routes.py` (after the last test function):

```python
@pytest.mark.asyncio
async def test_get_modes_returns_default(test_client, test_settings):
    """Any authenticated user can fetch the modes list."""
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)
    resp = await test_client.get(
        "/api/checkins/modes",
        cookies={"access_token": viewer_token},
    )
    assert resp.status_code == 200
    modes = resp.json()
    assert isinstance(modes, list)
    assert "Voice" in modes
    assert "Winlink" in modes
    assert len(modes) == 12
```

- [x] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "python -m pytest tests/test_checkin_routes.py::test_get_modes_returns_default -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with 404 (route doesn't exist yet)

- [x] **Step 3: Add the endpoint**

In `backend/modules/checkins/routes.py`, add the import and endpoint:

Add to imports:
```python
from backend.config_mgmt.service import get_checkin_modes
```

Add the route (before or after the existing routes):
```python
@checkins_router.get("/modes")
async def get_modes_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    return get_checkin_modes(db)
```

- [x] **Step 4: Run test to verify it passes**

Run: `nix-shell --run "python -m pytest tests/test_checkin_routes.py::test_get_modes_returns_default -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add backend/modules/checkins/routes.py tests/test_checkin_routes.py
git commit -m "feat: add GET /api/checkins/modes endpoint"
```

---

### Task 4: Frontend — fetch modes and use in Add Check-in dropdown

**Files:**
- Modify: `frontend/src/api/checkins.ts`
- Modify: `frontend/src/pages/CheckInsPage.tsx`

- [x] **Step 1: Add `fetchModes` to the API client**

In `frontend/src/api/checkins.ts`, add:

```typescript
export async function fetchModes(): Promise<string[]> {
  return apiFetch<string[]>("/checkins/modes");
}
```

- [x] **Step 2: Update AddCheckinModal to use dynamic modes**

In `frontend/src/pages/CheckInsPage.tsx`, add `fetchModes` to the import from `"../api/checkins"`:

```typescript
import {
  fetchSessionCheckins,
  scanMailbox,
  createManualCheckin,
  updateCheckin,
  approveSession,
  lookupCallsign,
  fetchRecentSessions,
  fetchModes,
} from "../api/checkins";
```

In the `CheckInsPage` function, add a state variable for modes (near the other state declarations):

```typescript
  const [modes, setModes] = useState<string[]>(["Voice", "Winlink", "CW", "Digital"]);
```

Add a `useEffect` to fetch modes on mount (near the other effects):

```typescript
  useEffect(() => {
    fetchModes()
      .then(setModes)
      .catch(() => {}); // Silently fall back to defaults
  }, []);
```

Pass `modes` to `AddCheckinModal` — update the component's props and the call site.

Update the `AddCheckinModal` component signature to accept `modes`:

```typescript
function AddCheckinModal({
  open,
  onClose,
  sessionId,
  onAdded,
  modes,
}: {
  open: boolean;
  onClose: () => void;
  sessionId: number;
  onAdded: () => void;
  modes: string[];
}) {
```

Replace the hardcoded `<select>` options in `AddCheckinModal`:

From:
```tsx
            <option>Voice</option>
            <option>Winlink</option>
            <option>CW</option>
            <option>Digital</option>
```

To:
```tsx
            {modes.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
```

Update the call site to pass `modes`:

```tsx
        <AddCheckinModal open={showAddModal} onClose={() => setShowAddModal(false)} sessionId={selectedSessionId} onAdded={loadCheckins} modes={modes} />
```

- [x] **Step 3: Verify build compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors

- [x] **Step 4: Commit**

```bash
git add frontend/src/api/checkins.ts frontend/src/pages/CheckInsPage.tsx
git commit -m "feat: fetch modes from API and use dynamic dropdown in Add Check-in"
```

---

### Task 5: Full Test Suite Verification

- [x] **Step 1: Run backend tests**

Run: `nix-shell --run "python -m pytest tests/ -x -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass

- [x] **Step 2: Run frontend type check**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors
