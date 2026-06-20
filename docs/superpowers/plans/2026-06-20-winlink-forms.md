# Winlink Standard Forms support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognize and parse `<RMS_Express_Form>` check-in messages, surface their fields in the check-in row, and render the original form (scripts disabled) in the edit modal. Standard Forms HTML library is fetched at admin request from the official Winlink download URL into a runtime state directory; parsing works without it, rendering degrades to a key-value HTML table.

**Architecture:** Backend gains a new `MessageType.WINLINK_FORM` enum value, a `parse_winlink_form_message` function that XML-parses the body and applies heuristic-plus-override field mapping (with Spec A's comma parser as a comments fallback), and a `forms` module that owns library distribution + read-only template rendering. Frontend gains a second `<details>` block in `EditCheckinModal` that iframes the server-rendered HTML, plus a small "Winlink Standard Forms" section on the config page with a fetch button.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / Alembic / `xml.etree.ElementTree` (stdlib) / `bleach` (new dep) on the backend; React 18 / TypeScript / Vite / Tailwind on the frontend.

## Global Constraints

- All Python commands run inside `.venv/`. `.venv/bin/pytest`, `.venv/bin/alembic`, `.venv/bin/python`. Lint with `nix-shell --run "ruff check"`.
- Ruff: line-length 120, `select = ["E", "F"]`. Production code must pass clean (pre-existing failures in `alembic/versions/` are unrelated).
- Conventional Commits with optional scope (`feat(forms)`, `fix(checkins)`, etc.).
- Frontend tooling: `cd frontend && nix-shell -p nodejs_22 --run "npm <cmd>"`.
- UI lists: no pagination; client-side filter/sort.
- The spec for this plan is `docs/superpowers/specs/2026-06-19-winlink-forms-design.md` — that document is the source of truth for behavior. This plan also implements two small deviations from the spec, documented in the relevant task interfaces:
  - **A new `state_dir` setting** is added to bring the spec's `${STATEDIR}` into existence (the Python `Settings` class had no such field; the NixOS module had `stateDir` but it wasn't exposed to the process).
  - **`form_view_html` is always server-rendered**, including the key-value fallback that the spec proposed implementing in the frontend. This keeps the frontend trivial — one iframe — and is fully consistent with the spec's stated principle "server-side rendering — frontend never does template substitution".
- After each task ends with a commit, run `.venv/bin/pytest -q` and `nix-shell --run "ruff check"`; both must be green before moving on.
- This plan is large enough that subagent-driven execution is strongly recommended over inline.

---

### Task 0: Branch + worktree setup

**Files:** None.

**Interfaces:**
- Consumes: clean `main` checkout, the merged Spec A work.
- Produces: a feature branch / worktree ready for the rest of the plan.

- [ ] **Step 1: Confirm main is clean and up to date**

```bash
cd /home/ku0hn/dev/SkyNetControl
git status
git pull --ff-only origin main
```

Expected: working tree clean, fast-forward only.

- [ ] **Step 2: Create the feature branch (or worktree)**

If using a worktree per project convention:

```bash
# Use the EnterWorktree native tool if your environment provides it.
# Otherwise:
git worktree add .claude/worktrees/winlink-forms -b feat/winlink-forms origin/main
cd .claude/worktrees/winlink-forms
```

If not using a worktree:

```bash
git checkout -b feat/winlink-forms
```

- [ ] **Step 3: Cherry-pick the spec into the worktree if the worktree branched from origin/main**

```bash
git log --oneline origin/main..main 2>/dev/null
# If that lists docs(forms): commits not yet on origin, cherry-pick them
# into the worktree branch so the spec is visible alongside the plan.
```

- [ ] **Step 4: Verify the venv is functional**

```bash
.venv/bin/pytest -q tests/test_message_parser.py
```

Expected: 23 tests pass (the Spec A parser tests). If `bad interpreter` or import errors appear, rebuild the venv:

```bash
rm -rf .venv && nix-shell --run :
```

No commit at the end of this task — the branch is the deliverable.

---

### Task 1: Add `state_dir` setting + NixOS wiring

**Files:**
- Modify: `backend/config.py` (add `state_dir` field)
- Modify: `module.nix` (export `SKYNET_STATE_DIR` to the systemd unit)
- Test: `tests/test_settings.py` (extend or create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `settings.state_dir: str` — defaults to `"."` (current dir). Set from `SKYNET_STATE_DIR` env var in production. Forms library and any future writable persistent state lives under here.

- [ ] **Step 1: Write the failing test**

Locate the existing settings tests; if there's a `tests/test_settings.py`, append. Otherwise create it. Pattern from `tests/test_message_parser.py` for style.

```python
import importlib
import pytest


def _reload_settings():
    """Re-import the settings module so a fresh Settings() reads current env."""
    import backend.config
    importlib.reload(backend.config)
    return backend.config.settings


def test_state_dir_defaults_to_cwd(monkeypatch):
    monkeypatch.delenv("SKYNET_STATE_DIR", raising=False)
    s = _reload_settings()
    assert s.state_dir == "."


def test_state_dir_reads_env(monkeypatch):
    monkeypatch.setenv("SKYNET_STATE_DIR", "/var/lib/skynetcontrol")
    s = _reload_settings()
    assert s.state_dir == "/var/lib/skynetcontrol"
```

- [ ] **Step 2: Run the failing tests**

```bash
.venv/bin/pytest -q tests/test_settings.py -k "state_dir"
```

Expected: FAIL — `state_dir` attribute doesn't exist.

- [ ] **Step 3: Add the field to `Settings`**

In `backend/config.py`, inside the `Settings` class, after `static_dir`:

```python
    state_dir: str = "."
```

The `env_prefix = "SKYNET_"` config means `SKYNET_STATE_DIR` maps automatically.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/pytest -q tests/test_settings.py -k "state_dir"
```

Expected: both PASS.

- [ ] **Step 5: Wire NixOS module to export `SKYNET_STATE_DIR`**

In `module.nix`, locate the `environment = {` block that sets `SKYNET_DATABASE_URL` (around line 161). Add a sibling line:

```nix
        SKYNET_STATE_DIR = cfg.stateDir;
```

That uses the existing `stateDir` option (default `/var/lib/skynetcontrol`) which is already created with correct ownership by the systemd-tmpfiles rule (line 152) and listed in `ReadWritePaths` (line 184). No additional NixOS plumbing needed.

- [ ] **Step 6: Run full suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green.

- [ ] **Step 7: Commit**

```bash
git add backend/config.py module.nix tests/test_settings.py
git commit -m "$(cat <<'EOF'
feat(config): add state_dir setting for runtime-writable storage

The forthcoming Winlink Standard Forms library lives in a runtime
state directory rather than the build-time static dir. The NixOS
module already had a stateDir option (/var/lib/skynetcontrol by
default) but the Python process had no way to read it. Add a
state_dir setting wired to SKYNET_STATE_DIR and export it from the
systemd unit so the Python app can resolve `${STATEDIR}/forms/` and
similar paths.

Defaults to "." for dev runs from the repo root.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add `WINLINK_FORM` to `MessageType` enum + migration

**Files:**
- Modify: `backend/modules/checkins/models.py:19-23` (extend `MessageType`)
- Create: `alembic/versions/<rev>_add_winlink_form_messagetype.py`
- Test: `tests/test_checkin_models.py` (add a small roundtrip test)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `MessageType.WINLINK_FORM = "winlink_form"` available for use by parser dispatch and model fields.

- [ ] **Step 1: Inspect the existing MessageType enum**

```bash
.venv/bin/python -c "from backend.modules.checkins.models import MessageType; print(list(MessageType))"
```

Expected: lists `FORM`, `PLAIN_TEXT`, `UNKNOWN`.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_checkin_models.py`:

```python
def test_winlink_form_message_type_persists(db):
    """A RawMessage with the new WINLINK_FORM enum value roundtrips through SQLite."""
    from datetime import datetime, timezone
    from backend.modules.checkins.models import MessageType, RawMessage

    msg = RawMessage(
        message_id="<wlf-1@example>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="check-in",
        body="<RMS_Express_Form><variables/></RMS_Express_Form>",
        message_type=MessageType.WINLINK_FORM,
        parsed=False,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    assert msg.message_type == MessageType.WINLINK_FORM
    assert msg.message_type.value == "winlink_form"
```

If the file uses a fixture name other than `db`, substitute the correct name (look at the file's other tests).

- [ ] **Step 3: Run the test to verify it fails**

```bash
.venv/bin/pytest -q tests/test_checkin_models.py -k "winlink_form_message_type"
```

Expected: FAIL — `WINLINK_FORM` does not exist.

- [ ] **Step 4: Extend the enum**

In `backend/modules/checkins/models.py`, line 19 onward:

```python
class MessageType(str, enum.Enum):
    FORM = "form"
    PLAIN_TEXT = "plain_text"
    UNKNOWN = "unknown"
    WINLINK_FORM = "winlink_form"
```

- [ ] **Step 5: Generate the Alembic migration**

```bash
.venv/bin/alembic revision -m "add winlink_form messagetype"
```

Expected output: prints the new file path.

- [ ] **Step 6: Fill in the migration body**

Open the new file. Confirm `down_revision` matches the current head:

```bash
.venv/bin/alembic heads
```

Replace the auto-generated `upgrade` / `downgrade` with:

```python
def upgrade() -> None:
    # SQLite stores Enum as VARCHAR(N), so adding a value is a no-op at the
    # DB layer — the Python enum extension in models.py is sufficient.
    # PostgreSQL stores it as a native enum type and requires ALTER TYPE.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'WINLINK_FORM'")


def downgrade() -> None:
    # PostgreSQL ENUM values cannot be removed without recreating the type.
    # This migration is one-way on Postgres; on SQLite it's already a no-op.
    pass
```

- [ ] **Step 7: Apply the migration**

```bash
.venv/bin/alembic upgrade head
```

Expected: `Running upgrade ... -> <rev>, add winlink_form messagetype`.

- [ ] **Step 8: Run the test to verify it passes**

```bash
.venv/bin/pytest -q tests/test_checkin_models.py -k "winlink_form_message_type"
```

Expected: PASS.

- [ ] **Step 9: Run full suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green.

- [ ] **Step 10: Commit**

```bash
git add backend/modules/checkins/models.py alembic/versions/*_add_winlink_form_messagetype.py tests/test_checkin_models.py
git commit -m "$(cat <<'EOF'
feat(checkins): add WINLINK_FORM MessageType enum value

Companion to the upcoming parse_winlink_form_message function. Plain
VARCHAR storage on SQLite (no schema change required); a guarded
ALTER TYPE on PostgreSQL.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `parse_winlink_form_message` and dispatcher hook

**Files:**
- Modify: `backend/modules/checkins/message_parser.py` (new function + dispatch + detection branch)
- Test: `tests/test_message_parser.py` (append a fixture body and the spec's enumerated cases)

**Interfaces:**
- Consumes:
  - `MessageType.WINLINK_FORM` from Task 2.
  - `parse_plain_text_message(body, known_modes)` (existing, Spec A) — used for comments re-parse and the malformed-XML fallthrough.
- Produces:
  - `parse_winlink_form_message(body: str, known_modes: set[str] | None = None) -> dict` — returns the same 10-key dict shape as `parse_plain_text_message` / `parse_form_message`.
  - `detect_message_type(body)` updated: a case-insensitive substring check for `<RMS_Express_Form>` runs first and returns `MessageType.WINLINK_FORM` on hit.
  - `parse_message(body, known_modes)` dispatches the new type to `parse_winlink_form_message`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_message_parser.py`. The canonical fixture is a synthetic Winlink Check-in form body; real Winlink templates use the same XML envelope structure.

```python
CHECKIN_FORM_BODY = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters>
    <xml_file_version>1.0</xml_file_version>
    <display_form>Winlink_Check_in.html</display_form>
  </form_parameters>
  <variables>
    <var name="Callsign">KU0HN</var>
    <var name="Operator">Ben Kuhn</var>
    <var name="City">Lewiston</var>
    <var name="County">Winona</var>
    <var name="State">MN</var>
    <var name="ModeOfCheckin">VHF Packet</var>
    <var name="Comments">via KU0HN-10</var>
    <var name="Latitude">44.0</var>
    <var name="Longitude">-91.8</var>
  </variables>
</RMS_Express_Form>
"""


def test_detect_winlink_form_body():
    """A body with <RMS_Express_Form> is detected before any other branch runs."""
    assert detect_message_type(CHECKIN_FORM_BODY) == MessageType.WINLINK_FORM


def test_parse_winlink_form_canonical():
    from backend.modules.checkins.message_parser import parse_winlink_form_message
    result = parse_winlink_form_message(CHECKIN_FORM_BODY, known_modes={"VHF Packet"})
    assert result["callsign"] == "KU0HN"
    assert result["name"] == "Ben Kuhn"
    assert result["city"] == "Lewiston"
    assert result["county"] == "Winona"
    assert result["state"] == "MN"
    assert result["mode"] == "VHF Packet"
    assert result["comments"] == "via KU0HN-10"
    assert result["latitude"] == 44.0
    assert result["longitude"] == -91.8
    assert result["confidence"] == "high"


def test_parse_winlink_form_heuristic_only():
    """Variable names that don't match the override map still resolve via heuristic substring."""
    from backend.modules.checkins.message_parser import parse_winlink_form_message
    body = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters><display_form>Something_Else.html</display_form></form_parameters>
  <variables>
    <var name="Senders_Callsign">W0ABC</var>
    <var name="Operator_Name">John</var>
    <var name="QTH_City">Denver</var>
    <var name="QTH_State">CO</var>
    <var name="Reporting_Mode">Voice</var>
  </variables>
</RMS_Express_Form>
"""
    result = parse_winlink_form_message(body, known_modes={"Voice"})
    assert result["callsign"] == "W0ABC"
    assert result["name"] == "John"
    assert result["city"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Voice"
    assert result["confidence"] == "high"


def test_parse_winlink_form_combined_location_splits():
    """A single 'location' variable comma-splits into city/county/state."""
    from backend.modules.checkins.message_parser import parse_winlink_form_message
    body = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters><display_form>x.html</display_form></form_parameters>
  <variables>
    <var name="Call">KU0HN</var>
    <var name="Name">Ben</var>
    <var name="Location">Lewiston, Winona, MN</var>
    <var name="Mode">VHF Packet</var>
  </variables>
</RMS_Express_Form>
"""
    result = parse_winlink_form_message(body, known_modes={"VHF Packet"})
    assert result["city"] == "Lewiston"
    assert result["county"] == "Winona"
    assert result["state"] == "MN"
    assert result["mode"] == "VHF Packet"


def test_parse_winlink_form_comments_reparse_fills_mode():
    """If mode is missing from variables but appears in comments, the re-parse picks it up."""
    from backend.modules.checkins.message_parser import parse_winlink_form_message
    body = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters><display_form>x.html</display_form></form_parameters>
  <variables>
    <var name="Callsign">KU0HN</var>
    <var name="Name">Ben</var>
    <var name="Comments">Ben, KU0HN, Lewiston, MN, Voice all good</var>
  </variables>
</RMS_Express_Form>
"""
    result = parse_winlink_form_message(body, known_modes={"Voice"})
    assert result["mode"] == "Voice"
    assert result["city"] == "Lewiston"
    assert result["state"] == "MN"
    # Confidence is medium because mode came from comments re-parse, not structured form.
    assert result["confidence"] == "medium"


def test_parse_winlink_form_malformed_xml_falls_through():
    """A body that looks like a winlink form but is broken XML falls through to plain-text."""
    body = "<RMS_Express_Form><variables><var name=callsign>oops, no quotes"
    msg_type, fields = parse_message(body, known_modes={"Voice"})
    # detect_message_type still returns WINLINK_FORM (substring matched),
    # but the parser falls through and dispatches to plain-text on the body.
    # The plain-text parser will degrade further (no commas in the way it expects),
    # so we mostly assert "doesn't raise" + low confidence.
    assert fields["confidence"] == "low"


def test_parse_winlink_form_non_form_body_unchanged():
    """A body with no <RMS_Express_Form> wrapper still goes through the Spec A paths."""
    body = "Ben, KU0HN, Lewiston, MN, Voice"
    msg_type, fields = parse_message(body, known_modes={"Voice"})
    assert msg_type == MessageType.PLAIN_TEXT
    assert fields["callsign"] == "KU0HN"


def test_parse_winlink_form_dispatched_by_parse_message():
    msg_type, fields = parse_message(CHECKIN_FORM_BODY, known_modes={"VHF Packet"})
    assert msg_type == MessageType.WINLINK_FORM
    assert fields["callsign"] == "KU0HN"
```

- [ ] **Step 2: Run the failing tests**

```bash
.venv/bin/pytest -q tests/test_message_parser.py -k "winlink or detect_winlink"
```

Expected: most fail — `parse_winlink_form_message` doesn't exist, `detect_message_type` doesn't know the new branch.

- [ ] **Step 3: Add the detection branch and parser function**

Edit `backend/modules/checkins/message_parser.py`. Two changes:

(1) At the top of `detect_message_type`, add the first branch:

```python
def detect_message_type(body: str) -> MessageType:
    """Detect whether the message body is a Winlink form, structured form, or plain text."""
    if "<rms_express_form>" in body.lower():
        return MessageType.WINLINK_FORM
    # ... existing logic unchanged below
```

(2) Add the new parser function above `parse_plain_text_message` (so the Spec A function stays its own paragraph):

```python
import xml.etree.ElementTree as ET


# Per-template override map. Filename keys are lowercased.
TEMPLATE_OVERRIDES: dict[str, dict[str, str]] = {
    "winlink_check_in.html": {
        "callsign": "callsign",
        "name": "operator",
        "city": "city",
        "county": "county",
        "state": "state",
        "mode": "modeofcheckin",
        "comments": "comments",
        "latitude": "latitude",
        "longitude": "longitude",
    },
}

# Heuristic patterns, ordered most-specific-first to avoid losing a value
# to a less-specific match (latitude before lat, longitude before lon).
_HEURISTIC_PATTERNS: dict[str, list[str]] = {
    "callsign": ["callsign", "call", "station"],
    "name": ["name", "operator"],
    "city": ["city"],
    "county": ["county", "parish", "borough"],
    "state": ["state", "province"],
    "mode": ["modeofcheckin", "mode"],  # specific first
    "comments": ["comments", "comment", "notes", "message"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "long", "lon"],
}

_LOCATION_VARIABLE_HINTS = ["location", "qth"]


def _parse_float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_winlink_form_message(body: str, known_modes: set[str] | None = None) -> dict:
    """Parse a Winlink Express form (`<RMS_Express_Form>` XML) check-in body.

    Falls back to `parse_plain_text_message` on malformed XML so we never
    silently drop a message.
    """
    if known_modes is None:
        known_modes = set()

    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        # Malformed XML — degrade to the plain-text path so the body is still
        # processed (low confidence, but not silently lost).
        return parse_plain_text_message(body, known_modes=known_modes)

    template_filename = ""
    df = root.find(".//form_parameters/display_form")
    if df is not None and df.text:
        template_filename = df.text.strip()

    # Lowercase variable names; preserve empty-string values (the spec
    # treats "" as a present-but-empty signal distinct from missing).
    variables: dict[str, str] = {}
    for var in root.findall(".//variables/var"):
        name = (var.get("name") or "").strip().lower()
        if not name:
            continue
        variables[name] = (var.text or "").strip()

    fields: dict[str, str | float | None] = {
        "name": "",
        "callsign": "",
        "city": None,
        "county": None,
        "state": None,
        "mode": "",
        "comments": None,
        "latitude": None,
        "longitude": None,
    }

    # Override pass.
    override = TEMPLATE_OVERRIDES.get(template_filename.lower())
    if override:
        for field, var_name in override.items():
            raw_value = variables.get(var_name.lower(), "")
            if not raw_value:
                continue
            if field in ("latitude", "longitude"):
                fields[field] = _parse_float_or_none(raw_value)
            elif field == "callsign":
                fields[field] = raw_value.upper()
            elif field in ("city", "county", "state", "comments"):
                fields[field] = raw_value or None
            else:
                fields[field] = raw_value

    # Heuristic pass — fill anything still unset.
    for field, patterns in _HEURISTIC_PATTERNS.items():
        # "Unset" means empty string for callsign/name/mode, None for the rest.
        if field in ("callsign", "name", "mode"):
            if fields[field]:
                continue
        else:
            if fields[field] is not None:
                continue

        for pattern in patterns:
            for var_name, var_value in variables.items():
                if pattern in var_name and var_value:
                    if field in ("latitude", "longitude"):
                        fields[field] = _parse_float_or_none(var_value)
                    elif field == "callsign":
                        fields[field] = var_value.upper()
                    elif field in ("city", "county", "state", "comments"):
                        fields[field] = var_value
                    else:
                        fields[field] = var_value
                    break
            if (field in ("callsign", "name", "mode") and fields[field]) or \
               (field not in ("callsign", "name", "mode") and fields[field] is not None):
                break

    # Combined-location fallback (only if city/county/state all still unset).
    if fields["city"] is None and fields["county"] is None and fields["state"] is None:
        for var_name, var_value in variables.items():
            if not var_value:
                continue
            if any(hint in var_name for hint in _LOCATION_VARIABLE_HINTS):
                parts = [p.strip() for p in var_value.split(",") if p.strip()]
                if len(parts) >= 3:
                    fields["city"], fields["county"], fields["state"] = parts[0], parts[1], parts[2]
                elif len(parts) == 2:
                    fields["city"], fields["state"] = parts[0], parts[1]
                elif len(parts) == 1:
                    fields["city"] = parts[0]
                break

    # Comments re-parse: if comments are present and any core field is still
    # missing, re-run Spec A's plain-text parser over the comments string and
    # merge in anything it produced.
    used_comments_reparse = False
    if fields["comments"]:
        missing_core = (
            not fields["name"]
            or not fields["callsign"]
            or not fields["mode"]
            or fields["city"] is None
            or fields["county"] is None
            or fields["state"] is None
        )
        if missing_core:
            reparse = parse_plain_text_message(fields["comments"], known_modes=known_modes)
            for field in ("name", "callsign", "city", "county", "state", "mode"):
                # Only fill if currently empty/None.
                empty = (
                    (field in ("name", "callsign", "mode") and not fields[field])
                    or (field in ("city", "county", "state") and fields[field] is None)
                )
                if empty:
                    reparse_value = reparse.get(field)
                    if reparse_value:
                        fields[field] = reparse_value
                        used_comments_reparse = True

    # Confidence.
    have_core = bool(fields["callsign"] and fields["name"] and fields["mode"])
    if have_core and not used_comments_reparse:
        confidence = "high"
    elif have_core and used_comments_reparse:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "name": fields["name"] or "",
        "callsign": fields["callsign"] or "",
        "city": fields["city"],
        "county": fields["county"],
        "state": fields["state"],
        "mode": fields["mode"] or "",
        "comments": fields["comments"],
        "latitude": fields["latitude"],
        "longitude": fields["longitude"],
        "confidence": confidence,
    }
```

(3) Update the `parse_message` dispatcher (bottom of the file) to handle the new type:

```python
def parse_message(body: str, known_modes: set[str] | None = None) -> tuple[MessageType, dict]:
    """Detect message type and parse accordingly."""
    msg_type = detect_message_type(body)
    if msg_type == MessageType.WINLINK_FORM:
        return msg_type, parse_winlink_form_message(body, known_modes=known_modes)
    if msg_type == MessageType.FORM:
        return msg_type, parse_form_message(body)
    if msg_type == MessageType.PLAIN_TEXT:
        return msg_type, parse_plain_text_message(body, known_modes=known_modes)
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

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/pytest -q tests/test_message_parser.py -k "winlink or detect_winlink"
```

Expected: all listed tests PASS.

- [ ] **Step 5: Run full suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/checkins/message_parser.py tests/test_message_parser.py
git commit -m "$(cat <<'EOF'
feat(checkins): parse <RMS_Express_Form> Winlink form check-ins

Adds detect_message_type branch for the XML envelope, a defensive
parse_winlink_form_message that maps form variables to check-in
fields via a per-template override map first and a substring
heuristic second, and re-runs Spec A's comma parser over the
comments variable to recover anything members typed into the comments
box. Malformed XML falls through to plain-text parsing so a broken
message is never silently dropped.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Standard Forms template renderer

**Files:**
- Modify: `pyproject.toml` (add `bleach` dep)
- Create: `backend/modules/forms/__init__.py` (empty)
- Create: `backend/modules/forms/library.py` (statedir helpers, template lookup)
- Create: `backend/modules/forms/render.py` (`render_form_view`)
- Test: `tests/test_form_render.py` (new)

**Interfaces:**
- Consumes:
  - `settings.state_dir` from Task 1.
- Produces:
  - `backend.modules.forms.library.forms_library_dir() -> pathlib.Path` — returns `Path(settings.state_dir) / "forms"`.
  - `backend.modules.forms.library.find_template(filename: str) -> pathlib.Path | None` — walks `forms_library_dir()` for a file with the given basename (case-insensitive). Returns None if the directory doesn't exist or the file isn't found. Uses a process-level cache (cleared by `clear_template_cache()` for tests + by Task 5's fetch endpoint after a successful download).
  - `backend.modules.forms.library.clear_template_cache() -> None` — for tests + post-fetch cache invalidation.
  - `backend.modules.forms.render.render_form_view(template_filename: str, variables: dict[str, str]) -> str | None` — returns sanitized HTML for the form. Strategy: if `find_template(template_filename)` returns a path, substitute the variables into the template HTML using the Winlink syntax (discovered in this task — see step 1) and sanitize. If no template is found OR substitution can't be performed, render a small key-value HTML table fallback (still sanitized). Returns None only when both `template_filename` is missing/empty AND `variables` is empty.

- [ ] **Step 1: Discover the Winlink template substitution syntax**

Before writing the renderer, look at the real templates to see what placeholder syntax they use. Run, from the worktree root:

```bash
mkdir -p /tmp/wl-forms && cd /tmp/wl-forms && \
  curl -sSL -o Standard_Forms.zip "https://downloads.winlink.org/User%20Programs/Standard_Forms.zip" && \
  unzip -q Standard_Forms.zip && \
  find . -iname "*check_in*.html" -o -iname "*checkin*.html" | head -5
```

If `curl` is unavailable in the sandbox, ask the user to run it interactively (`! curl -sSL -o ... ...`) and report the discovered template path.

Open one of the matching files in `Read` and look for the substitution markup. Winlink Express templates typically substitute via one of:
- `{var_name}` literal-brace tokens
- `<input … name="var_name" value="...">` (Express writes the value attribute when rendering)
- A `<script>` block that DOM-mutates after load (these can't be rendered without JS — they degrade to the key-value fallback)

Document what you find in `backend/modules/forms/render.py`'s module docstring (one short paragraph) so future maintainers don't have to re-derive it.

If the templates use a syntax not covered above, STOP and report DONE_WITH_CONCERNS — the renderer implementation will need design input.

- [ ] **Step 2: Add the `bleach` dependency**

In `pyproject.toml`, add to `dependencies` (line 10-24):

```python
    "bleach>=6.0.0",
```

Then install into the venv:

```bash
nix-shell --run ":"
```

Verify:

```bash
.venv/bin/python -c "import bleach; print(bleach.__version__)"
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_form_render.py`. Adapt the substitution-test fixture below to the syntax discovered in step 1 — if templates use `{var_name}`, the fixture below works as-is; if they use `<input … value="…">`, swap the fixture accordingly.

```python
import pytest
from pathlib import Path


def test_render_substitutes_into_template(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    # Make forms_library_dir point at tmp_path.
    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "Test_Check_in.html").write_text(
        "<html><body>Callsign: {callsign}, Name: {name}</body></html>"
    )

    html = render.render_form_view("Test_Check_in.html", {"callsign": "KU0HN", "name": "Ben"})
    assert html is not None
    assert "KU0HN" in html
    assert "Ben" in html


def test_render_sanitizes_script_tags(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "Bad.html").write_text(
        "<html><body><script>alert(1)</script>Hi {name}</body></html>"
    )

    html = render.render_form_view("Bad.html", {"name": "Ben"})
    assert html is not None
    assert "<script>" not in html
    assert "alert(1)" not in html
    assert "Ben" in html


def test_render_strips_event_handlers_and_javascript_urls(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "Bad.html").write_text(
        '<html><body><a href="javascript:alert(1)" onclick="alert(2)">click {x}</a></body></html>'
    )

    html = render.render_form_view("Bad.html", {"x": "test"})
    assert html is not None
    assert "javascript:" not in html
    assert "onclick" not in html


def test_render_missing_template_returns_kv_fallback(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    # forms/ dir does not exist.
    html = render.render_form_view("Nonexistent.html", {"callsign": "KU0HN", "name": "Ben"})
    assert html is not None  # KV fallback rendered
    assert "KU0HN" in html
    assert "Ben" in html
    # Variable names appear as labels in the table.
    assert "callsign" in html.lower()


def test_render_no_template_no_variables_returns_none(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    html = render.render_form_view("Nonexistent.html", {})
    assert html is None


def test_find_template_case_insensitive(tmp_path, monkeypatch):
    from backend.modules.forms import library
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "MyTemplate.html").write_text("<html/>")

    found = library.find_template("mytemplate.html")
    assert found is not None
    assert found.name == "MyTemplate.html"


def test_find_template_walks_nested_dirs(tmp_path, monkeypatch):
    from backend.modules.forms import library
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    nested = tmp_path / "forms" / "Standard Forms" / "Generic"
    nested.mkdir(parents=True)
    (nested / "Buried.html").write_text("<html/>")

    found = library.find_template("Buried.html")
    assert found is not None
    assert "Generic" in str(found)
```

- [ ] **Step 4: Run the failing tests**

```bash
.venv/bin/pytest -q tests/test_form_render.py
```

Expected: import errors (module doesn't exist).

- [ ] **Step 5: Implement `backend/modules/forms/library.py`**

```python
"""Helpers for locating Winlink Standard Forms templates on disk."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from backend.config import settings


_cache_lock = threading.Lock()
_template_index: dict[str, Path] | None = None  # lowercased basename → path


def forms_library_dir() -> Path:
    """Resolve the on-disk directory where the forms library is unpacked."""
    return Path(settings.state_dir) / "forms"


def clear_template_cache() -> None:
    """Drop the in-process index of available templates."""
    global _template_index
    with _cache_lock:
        _template_index = None


def _build_index() -> dict[str, Path]:
    base = forms_library_dir()
    index: dict[str, Path] = {}
    if not base.is_dir():
        return index
    for path in base.rglob("*"):
        if path.is_file():
            index.setdefault(path.name.lower(), path)
    return index


def find_template(filename: str) -> Path | None:
    """Return the on-disk path for a template basename, case-insensitive."""
    global _template_index
    if not filename:
        return None
    with _cache_lock:
        if _template_index is None:
            _template_index = _build_index()
        return _template_index.get(filename.lower())
```

- [ ] **Step 6: Implement `backend/modules/forms/render.py`**

Use the substitution syntax discovered in step 1. The example below assumes `{var_name}` tokens (standard Winlink form syntax); if step 1 reveals a different syntax, adapt the `_substitute` function accordingly.

```python
"""Render Winlink form templates to read-only sanitized HTML.

Winlink Express templates use `{variable_name}` tokens that are
substituted with the value of the corresponding <var> element from the
form's <variables> block. (See `backend/modules/forms/render.py`'s
companion docstring above for any syntax variants discovered during
implementation.) Templates that rely on JS for rendering can't produce
useful read-only output and fall back to the key-value table.
"""
from __future__ import annotations

import re
from html import escape

import bleach

from backend.modules.forms.library import find_template


_ALLOWED_TAGS = [
    "div", "span", "p",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "td", "th",
    "dl", "dt", "dd",
    "ul", "ol", "li",
    "br", "hr",
    "b", "i", "u", "strong", "em",
    "label", "input",
    "html", "body", "head", "title", "meta", "style",
]
_ALLOWED_ATTRS = {
    "*": ["class", "style", "id"],
    "input": ["type", "name", "value", "readonly", "disabled"],
    "label": ["for"],
    "meta": ["charset", "name", "content"],
}

# {var_name} placeholders, allowing alphanumeric + underscore.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _substitute(template_html: str, variables: dict[str, str]) -> str:
    """Replace {var_name} tokens with the corresponding variable value.

    Missing variables produce empty strings (not the literal token).
    Values are not HTML-escaped here — sanitization runs on the full
    rendered document afterward.
    """
    lookup = {k.lower(): v for k, v in variables.items()}

    def replace(match: re.Match) -> str:
        name = match.group(1).lower()
        return lookup.get(name, "")

    return _PLACEHOLDER_RE.sub(replace, template_html)


def _render_kv_fallback(variables: dict[str, str]) -> str:
    """Build a minimal HTML table from variable name/value pairs.

    Used when the template isn't on disk. Variable names + values are
    HTML-escaped before composition; the result is also run through the
    sanitizer for defense-in-depth.
    """
    rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{escape(value)}</td></tr>"
        for name, value in variables.items()
    )
    return (
        '<!DOCTYPE html>'
        '<html><head>'
        '<meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">'
        '<style>body { font-family: sans-serif; padding: 1em; } '
        'table { border-collapse: collapse; } '
        'td { padding: 4px 8px; border-bottom: 1px solid #ddd; } '
        'td:first-child { font-weight: 600; color: #555; }</style>'
        '</head><body>'
        '<table><tbody>' + rows + '</tbody></table>'
        '</body></html>'
    )


def _sanitize(html: str) -> str:
    """Pass the rendered HTML through bleach with the allowlist above."""
    return bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        strip=True,
        strip_comments=True,
    )


def render_form_view(template_filename: str, variables: dict[str, str]) -> str | None:
    """Render a Winlink form to read-only sanitized HTML.

    Returns None only when there is nothing to show (no template AND no
    variables). When the named template is missing from disk OR when
    rendering it fails, returns a sanitized key-value HTML table built
    from `variables`.
    """
    path = find_template(template_filename) if template_filename else None

    if path is not None:
        try:
            template_html = path.read_text(errors="replace")
            substituted = _substitute(template_html, variables)
            return _sanitize(substituted)
        except OSError:
            # File disappeared between index build and read — fall through.
            pass

    if not variables:
        return None
    return _sanitize(_render_kv_fallback(variables))
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
.venv/bin/pytest -q tests/test_form_render.py
```

Expected: all PASS.

- [ ] **Step 8: Run full suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml backend/modules/forms/ tests/test_form_render.py
git commit -m "$(cat <<'EOF'
feat(forms): server-side renderer for Winlink form templates

New module backend.modules.forms with two pieces:
- library.find_template walks ${SKYNET_STATE_DIR}/forms/ for templates
  by basename (case-insensitive), cached per process and invalidated
  after a library fetch.
- render.render_form_view substitutes the form's <variables> into the
  template HTML and sanitizes via bleach. When the template isn't on
  disk, builds a key-value HTML table from the variables instead — both
  paths go through the same sanitizer with a tight allowlist.

The frontend iframes the result with sandbox=""; the rendered output
also embeds a CSP meta tag (default-src 'none'; style-src
'unsafe-inline'; img-src data:) for defense in depth.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Forms library fetch endpoint

**Files:**
- Create: `backend/modules/forms/fetch.py` (download + extract logic)
- Create: `backend/modules/forms/routes.py` (`POST /api/config/forms/fetch`, `GET /api/config/forms/status`)
- Modify: `backend/app.py` (register the router)
- Modify: `backend/config_mgmt/service.py` (read forms.* keys)
- Test: `tests/test_forms_fetch.py` (new)

**Interfaces:**
- Consumes:
  - `settings.state_dir` from Task 1.
  - `backend.modules.forms.library.clear_template_cache` from Task 4.
  - The existing SSRF guard helpers in `backend/auth/service.py`: `_ssrf_guard_discovery_url_async(url)` and the pin pattern from `fetch_oidc_discovery` (see `backend/auth/service.py:135-155`).
  - `backend.auth.dependencies.require_role(UserRole.ADMIN)` for endpoint protection.
  - `backend.config_mgmt.service.get_config_value` / `set_config_value` for AppConfig reads/writes.
- Produces:
  - `POST /api/config/forms/fetch` — admin-only; downloads and unpacks the forms library; returns `{version, last_fetched_at}` on success or 4xx/5xx with an error.
  - `GET /api/config/forms/status` — returns the current `forms.library_version`, `forms.last_fetched_at`, `forms.source_url`.
  - AppConfig keys: `forms.source_url` (default `https://downloads.winlink.org/User%20Programs/Standard_Forms.zip`), `forms.library_version`, `forms.last_fetched_at`.

- [ ] **Step 1: Build small ZIP fixtures**

Create `tests/fixtures/forms/` with two byte-literal ZIPs (use Python in step 3 to construct them) — one valid, one with a zip-slip path. A zip-bomb is too big to commit; build it on the fly in the test.

This step is just to plan the structure; the actual files are created inside the test setup.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_forms_fetch.py`:

```python
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import Response


def _make_zip(entries: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP with the given {arcname: content} entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for arcname, content in entries.items():
            zf.writestr(arcname, content)
    return buf.getvalue()


@pytest.fixture
def forms_state_dir(tmp_path, monkeypatch):
    from backend.config import settings
    from backend.modules.forms import library
    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    return tmp_path


async def test_fetch_requires_admin(client, regular_user_token, forms_state_dir):
    """Non-admin tokens get 403."""
    resp = await client.post(
        "/api/config/forms/fetch",
        headers={"Authorization": f"Bearer {regular_user_token}"},
    )
    assert resp.status_code in (401, 403)


async def test_fetch_success_writes_library_and_updates_config(
    client, admin_token, forms_state_dir, db, monkeypatch
):
    from backend.modules.forms import fetch as forms_fetch
    from backend.config_mgmt.service import get_config_value

    zip_bytes = _make_zip({
        "Standard Forms/Generic/Test.html": b"<html><body>{callsign}</body></html>",
        "Standard Forms/README.txt": b"plain text",
    })

    async def fake_download(url, *, max_size_bytes):
        return zip_bytes, "Standard_Forms_1.2.3.zip"

    monkeypatch.setattr(forms_fetch, "_download_zip", fake_download)

    resp = await client.post(
        "/api/config/forms/fetch",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["library_version"]  # version present, exact format up to implementation
    assert payload["last_fetched_at"]

    forms_dir = forms_state_dir / "forms"
    assert forms_dir.is_dir()
    assert (forms_dir / "Standard Forms" / "Generic" / "Test.html").exists()
    assert (forms_dir / "Standard Forms" / "README.txt").exists()

    assert get_config_value(db, "forms.library_version") == payload["library_version"]
    assert get_config_value(db, "forms.last_fetched_at") == payload["last_fetched_at"]


async def test_fetch_rejects_oversize_zip(
    client, admin_token, forms_state_dir, monkeypatch
):
    from backend.modules.forms import fetch as forms_fetch

    # Build a zip whose uncompressed total exceeds the cap (200 MB).
    big_content = b"A" * (1024 * 1024)  # 1 MB
    entries = {f"big-{i}.txt": big_content for i in range(201)}  # >200 MB
    zip_bytes = _make_zip(entries)

    async def fake_download(url, *, max_size_bytes):
        return zip_bytes, "Standard_Forms.zip"

    monkeypatch.setattr(forms_fetch, "_download_zip", fake_download)

    resp = await client.post(
        "/api/config/forms/fetch",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "size" in resp.text.lower() or "limit" in resp.text.lower()


async def test_fetch_rejects_zip_slip(
    client, admin_token, forms_state_dir, monkeypatch
):
    from backend.modules.forms import fetch as forms_fetch

    zip_bytes = _make_zip({
        "../../../etc/passwd": b"pwned",
        "Standard Forms/OK.html": b"<html/>",
    })

    async def fake_download(url, *, max_size_bytes):
        return zip_bytes, "Standard_Forms.zip"

    monkeypatch.setattr(forms_fetch, "_download_zip", fake_download)

    resp = await client.post(
        "/api/config/forms/fetch",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


async def test_fetch_drops_script_entries(
    client, admin_token, forms_state_dir, monkeypatch
):
    from backend.modules.forms import fetch as forms_fetch

    zip_bytes = _make_zip({
        "Standard Forms/script.js": b"alert(1)",
        "Standard Forms/script.exe": b"\x4dZ junk",
        "Standard Forms/Good.html": b"<html/>",
    })

    async def fake_download(url, *, max_size_bytes):
        return zip_bytes, "Standard_Forms.zip"

    monkeypatch.setattr(forms_fetch, "_download_zip", fake_download)

    resp = await client.post(
        "/api/config/forms/fetch",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    forms_dir = forms_state_dir / "forms"
    assert (forms_dir / "Standard Forms" / "Good.html").exists()
    assert not (forms_dir / "Standard Forms" / "script.js").exists()
    assert not (forms_dir / "Standard Forms" / "script.exe").exists()


async def test_fetch_failure_leaves_prior_library_intact(
    client, admin_token, forms_state_dir, monkeypatch
):
    """If the fetch fails mid-way, the existing forms/ directory must not be partially overwritten."""
    from backend.modules.forms import fetch as forms_fetch

    # Seed an existing forms/ directory.
    existing = forms_state_dir / "forms" / "Standard Forms"
    existing.mkdir(parents=True)
    (existing / "Existing.html").write_text("<html>old</html>")

    async def fake_download(url, *, max_size_bytes):
        raise ValueError("upstream unreachable")

    monkeypatch.setattr(forms_fetch, "_download_zip", fake_download)

    resp = await client.post(
        "/api/config/forms/fetch",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code in (502, 500)

    # Existing library survives.
    assert (forms_state_dir / "forms" / "Standard Forms" / "Existing.html").exists()


async def test_status_endpoint_returns_current_config(
    client, admin_token, forms_state_dir, db
):
    from backend.config_mgmt.service import set_config_value

    set_config_value(db, "forms.library_version", "1.2.3")
    set_config_value(db, "forms.last_fetched_at", "2026-06-20T12:00:00+00:00")

    resp = await client.get(
        "/api/config/forms/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["library_version"] == "1.2.3"
    assert payload["last_fetched_at"] == "2026-06-20T12:00:00+00:00"
    assert payload["source_url"]  # default present
```

If the test file uses fixture names other than `client`, `admin_token`, `regular_user_token`, `db`, look at `tests/test_checkin_routes.py` for the conventions used.

- [ ] **Step 3: Run the failing tests**

```bash
.venv/bin/pytest -q tests/test_forms_fetch.py
```

Expected: import errors / 404 from missing routes.

- [ ] **Step 4: Implement `backend/modules/forms/fetch.py`**

```python
"""Download + validate + extract the Winlink Standard Forms ZIP."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from backend.auth.dns_pin import pin_dns
from backend.auth.service import _ssrf_guard_discovery_url_async
from backend.config import settings
from backend.modules.forms.library import clear_template_cache, forms_library_dir

logger = logging.getLogger(__name__)


DEFAULT_SOURCE_URL = "https://downloads.winlink.org/User%20Programs/Standard_Forms.zip"
DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
DEFAULT_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MB
DEFAULT_MAX_ENTRY_COUNT = 5000

ALLOWED_EXTENSIONS = {".html", ".htm", ".txt", ".xml", ".css"}

ZIP_MAGIC = b"PK\x03\x04"

_VERSION_RE = re.compile(r"_(\d+(?:\.\d+){1,3})\.zip$", re.IGNORECASE)


class FormsFetchError(Exception):
    """Raised when the forms library cannot be fetched or extracted."""


def _derive_version(filename: str, content_sha256: str) -> str:
    """Pull a version string from the filename if present; else use a SHA prefix."""
    m = _VERSION_RE.search(filename or "")
    if m:
        return m.group(1)
    return content_sha256[:12]


async def _download_zip(url: str, *, max_size_bytes: int) -> tuple[bytes, str]:
    """SSRF-guarded HTTPS download of a ZIP. Returns (bytes, served_filename)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise FormsFetchError(f"forms.source_url must use https:// (got {parsed.scheme or '(none)'})")

    host, ip = await _ssrf_guard_discovery_url_async(url)

    with pin_dns(host, ip):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content
            if len(content) > max_size_bytes:
                raise FormsFetchError(f"download exceeded max size {max_size_bytes} bytes")

    # Determine served filename (URL basename takes precedence; httpx doesn't
    # always surface Content-Disposition cleanly).
    served_filename = os.path.basename(parsed.path) or "Standard_Forms.zip"
    return content, served_filename


def _validate_and_extract(zip_bytes: bytes, dest_root: Path) -> None:
    """Extract a validated ZIP into dest_root (which must already exist and be empty)."""
    if not zip_bytes.startswith(ZIP_MAGIC):
        raise FormsFetchError("downloaded content is not a ZIP archive")

    try:
        zf = zipfile.ZipFile(io_from_bytes(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise FormsFetchError(f"invalid ZIP: {exc}") from exc

    entries = zf.infolist()
    if len(entries) > DEFAULT_MAX_ENTRY_COUNT:
        raise FormsFetchError(f"ZIP entry count {len(entries)} exceeds {DEFAULT_MAX_ENTRY_COUNT}")

    total = sum(info.file_size for info in entries)
    if total > DEFAULT_MAX_UNCOMPRESSED_BYTES:
        raise FormsFetchError(f"ZIP uncompressed size {total} exceeds {DEFAULT_MAX_UNCOMPRESSED_BYTES}")

    dest_real = dest_root.resolve()
    for info in entries:
        if info.is_dir():
            continue
        # Drop disallowed extensions silently.
        ext = os.path.splitext(info.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue

        # Zip-slip guard.
        target = (dest_root / info.filename).resolve()
        try:
            target.relative_to(dest_real)
        except ValueError:
            raise FormsFetchError(f"ZIP entry escapes destination: {info.filename!r}")

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def io_from_bytes(data: bytes):
    """Wrap bytes for zipfile.ZipFile (broken out to keep it monkeypatchable)."""
    import io
    return io.BytesIO(data)


async def fetch_and_install(url: str, *, max_size_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES) -> dict:
    """End-to-end: download, validate, extract, atomic-promote, update cache."""
    zip_bytes, served_filename = await _download_zip(url, max_size_bytes=max_size_bytes)

    state_dir = Path(settings.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Stage into a temp dir SIBLING of forms_library_dir() so the atomic
    # rename works (rename across filesystems is not atomic).
    final_dir = forms_library_dir()
    with tempfile.TemporaryDirectory(prefix="forms-new-", dir=str(state_dir)) as staging:
        staging_path = Path(staging)
        _validate_and_extract(zip_bytes, staging_path)

        # Promote: rename existing forms/ aside, move new in, then rm the old one.
        backup_path = state_dir / "forms.old"
        if backup_path.exists():
            shutil.rmtree(backup_path)
        if final_dir.exists():
            final_dir.rename(backup_path)
        # tempfile.TemporaryDirectory will try to clean up; rename it out first.
        shutil.move(str(staging_path), str(final_dir))
        # Recreate a placeholder so the context manager's cleanup doesn't error.
        staging_path.mkdir(exist_ok=True)
        if backup_path.exists():
            shutil.rmtree(backup_path, ignore_errors=True)

    clear_template_cache()

    content_sha = hashlib.sha256(zip_bytes).hexdigest()
    version = _derive_version(served_filename, content_sha)
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    return {"library_version": version, "last_fetched_at": now_iso}
```

- [ ] **Step 5: Implement `backend/modules/forms/routes.py`**

```python
"""Admin endpoints for managing the Winlink Standard Forms library."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, require_role
from backend.auth.models import UserRole
from backend.config_mgmt.service import get_config_value, set_config_value
from backend.modules.forms.fetch import (
    DEFAULT_SOURCE_URL,
    FormsFetchError,
    fetch_and_install,
)

forms_router = APIRouter(prefix="/api/config/forms", tags=["forms"])


@forms_router.get("/status")
async def get_forms_status(
    db: Session = Depends(get_db_session),
    _user=Depends(require_role(UserRole.ADMIN)),
) -> dict:
    return {
        "source_url": get_config_value(db, "forms.source_url") or DEFAULT_SOURCE_URL,
        "library_version": get_config_value(db, "forms.library_version"),
        "last_fetched_at": get_config_value(db, "forms.last_fetched_at"),
    }


@forms_router.post("/fetch")
async def fetch_forms_library(
    db: Session = Depends(get_db_session),
    _user=Depends(require_role(UserRole.ADMIN)),
) -> dict:
    source_url = get_config_value(db, "forms.source_url") or DEFAULT_SOURCE_URL
    try:
        result = await fetch_and_install(source_url)
    except FormsFetchError as exc:
        # Validation / extraction failures are 400 (operator's mistake or
        # upstream serving garbage); SSRF guard failures bubble up here too.
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        # Network errors, timeouts: 502.
        raise HTTPException(status_code=502, detail=f"failed to fetch forms library: {exc}")

    set_config_value(db, "forms.library_version", result["library_version"])
    set_config_value(db, "forms.last_fetched_at", result["last_fetched_at"])
    db.commit()

    return result
```

- [ ] **Step 6: Register the router in `backend/app.py`**

Find the block where other routers are included (search for `include_router`). Add:

```python
from backend.modules.forms.routes import forms_router
app.include_router(forms_router)
```

Place near the other config-related includes.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
.venv/bin/pytest -q tests/test_forms_fetch.py
```

Expected: all PASS. If admin/regular fixture names differ, update the test file to match.

- [ ] **Step 8: Run full suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green.

- [ ] **Step 9: Commit**

```bash
git add backend/modules/forms/fetch.py backend/modules/forms/routes.py backend/app.py tests/test_forms_fetch.py
git commit -m "$(cat <<'EOF'
feat(forms): admin endpoint to fetch the Standard Forms library

POST /api/config/forms/fetch (admin-only) downloads the official
Winlink Standard Forms ZIP through the existing SSRF-guarded HTTP
path (DNS-pinned, https-only), validates the archive (ZIP magic, max
size 50 MB downloaded / 200 MB uncompressed / 5000 entries),
rejects zip-slip paths, drops non-HTML/CSS/TXT/XML entries, and
atomically promotes the new tree into ${SKYNET_STATE_DIR}/forms/.

GET /api/config/forms/status returns the current source_url,
library_version, and last_fetched_at for display in the admin UI.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Expose `form_view_html` on the check-in API

**Files:**
- Modify: `backend/modules/checkins/routes.py:52-69` (extend `_checkin_to_response`)
- Test: `tests/test_checkin_routes.py` (add cases)

**Interfaces:**
- Consumes:
  - `parse_winlink_form_message` from Task 3 (re-parses XML on demand for variable extraction).
  - `render_form_view` from Task 4.
- Produces:
  - `_checkin_to_response` payload gains:
    - `raw_message.message_type: str | null` — already on the model; surface it.
    - `form_view_html: str | null` — present only when `raw_message.message_type == "winlink_form"` and the renderer produces output. Null otherwise.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_checkin_routes.py`:

```python
async def test_winlink_form_checkin_response_includes_form_view_html(
    client, auth_headers, db, seeded_session_id, tmp_path, monkeypatch
):
    """A winlink_form check-in surfaces form_view_html + raw_message.message_type."""
    from datetime import datetime, timezone
    from backend.config import settings
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from backend.modules.forms import library

    # Make forms_library_dir resolve to a tmp dir + seed a fake template
    # so the renderer returns the template path, not the KV fallback.
    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "Test_Check_in.html").write_text("<html><body>{callsign}</body></html>")

    body = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters><display_form>Test_Check_in.html</display_form></form_parameters>
  <variables>
    <var name="Callsign">KU0HN</var>
    <var name="Name">Ben</var>
    <var name="Mode">Voice</var>
  </variables>
</RMS_Express_Form>"""

    raw = RawMessage(
        message_id="<wlf@x>",
        from_address="ku0hn@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="check-in",
        body=body,
        message_type=MessageType.WINLINK_FORM,
        parsed=True,
    )
    db.add(raw)
    db.flush()
    db.add(CheckIn(
        session_id=seeded_session_id,
        raw_message_id=raw.id,
        callsign="KU0HN",
        name="Ben",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))
    db.commit()

    resp = await client.get(
        f"/api/checkins/by-session/{seeded_session_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["callsign"] == "KU0HN")
    assert row["raw_message"]["message_type"] == "winlink_form"
    assert row["form_view_html"] is not None
    assert "KU0HN" in row["form_view_html"]


async def test_non_winlink_checkin_response_has_null_form_view_html(
    client, auth_headers, db, seeded_session_id
):
    from datetime import datetime, timezone
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus

    raw = RawMessage(
        message_id="<pt@x>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="check-in",
        body="W0ABC, John, Denver, CO, Voice",
        message_type=MessageType.PLAIN_TEXT,
        parsed=True,
    )
    db.add(raw)
    db.flush()
    db.add(CheckIn(
        session_id=seeded_session_id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="John",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))
    db.commit()

    resp = await client.get(
        f"/api/checkins/by-session/{seeded_session_id}",
        headers=auth_headers,
    )
    row = next(r for r in resp.json() if r["callsign"] == "W0ABC")
    assert row["raw_message"]["message_type"] == "plain_text"
    assert row["form_view_html"] is None
```

Use the same fixture names the file already uses; adapt if needed.

- [ ] **Step 2: Run the failing tests**

```bash
.venv/bin/pytest -q tests/test_checkin_routes.py -k "form_view_html"
```

Expected: FAIL — `form_view_html` not in payload; `message_type` not in `raw_message` payload.

- [ ] **Step 3: Extend the serializer**

In `backend/modules/checkins/routes.py`, replace `_checkin_to_response` (lines 52-69) with:

```python
def _checkin_to_response(checkin: CheckIn) -> dict:
    raw = checkin.raw_message
    raw_payload: dict | None
    form_view_html: str | None = None
    if raw is None:
        raw_payload = None
    else:
        raw_payload = {
            "subject": raw.subject,
            "from_address": raw.from_address,
            "received_at": raw.received_at.isoformat(),
            "body": raw.body,
            "message_type": raw.message_type.value,
        }
        if raw.message_type == MessageType.WINLINK_FORM:
            form_view_html = _render_winlink_form_view(raw.body)

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
        "raw_message": raw_payload,
        "form_view_html": form_view_html,
    }


def _render_winlink_form_view(body: str) -> str | None:
    """Best-effort render a winlink form body. Never raises."""
    import xml.etree.ElementTree as ET
    from backend.modules.forms.render import render_form_view

    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    template_filename = ""
    df = root.find(".//form_parameters/display_form")
    if df is not None and df.text:
        template_filename = df.text.strip()
    variables: dict[str, str] = {}
    for var in root.findall(".//variables/var"):
        name = (var.get("name") or "").strip()
        if not name:
            continue
        variables[name] = (var.text or "").strip()
    return render_form_view(template_filename, variables)
```

Add `MessageType` to the `from backend.modules.checkins.models import ...` block at the top of the file if it's not already imported.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/pytest -q tests/test_checkin_routes.py -k "form_view_html"
```

Expected: both PASS.

- [ ] **Step 5: Run full suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green. If any existing route test snapshot pinned the response keys, update to include `form_view_html: null` and `raw_message.message_type` where applicable.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/checkins/routes.py tests/test_checkin_routes.py
git commit -m "$(cat <<'EOF'
feat(checkins): serve form_view_html for winlink_form check-ins

The check-in payload now exposes raw_message.message_type and a new
form_view_html field. For winlink_form rows the field carries the
server-rendered HTML view of the form (template substitution +
sanitization, or a KV-table fallback when the template isn't on
disk); for every other row it is null.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Frontend — type extension, edit-modal form view, admin fetch UI

**Files:**
- Modify: `frontend/src/types/index.ts` (extend `CheckIn`)
- Modify: `frontend/src/api/checkins.ts` (no shape change beyond the type; verify import)
- Create: `frontend/src/api/forms.ts` (admin fetch/status API)
- Modify: `frontend/src/pages/CheckInsPage.tsx` (add "Form view" `<details>` block in `EditCheckinModal`)
- Modify: `frontend/src/pages/AppConfigPage.tsx` (or whichever page is the admin config — find via grep) to add a "Winlink Standard Forms" section.

**Interfaces:**
- Consumes:
  - `CheckIn.form_view_html` and `CheckIn.raw_message.message_type` from Task 6.
  - `GET /api/config/forms/status` and `POST /api/config/forms/fetch` from Task 5.
- Produces:
  - No new exports beyond `frontend/src/api/forms.ts` (which is internal to the page).

- [ ] **Step 1: Locate the admin config page**

```bash
grep -rln "config\|AppConfig\|admin" frontend/src/pages/ | head -10
```

Open the file that hosts settings management (likely `AppConfigPage.tsx` or `ConfigPage.tsx`). Confirm a section can be added there following its existing pattern.

- [ ] **Step 2: Extend the TypeScript type**

In `frontend/src/types/index.ts`, replace the `CheckIn` interface to add the two new fields:

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
  raw_message: {
    subject: string;
    from_address: string;
    received_at: string;
    body: string;
    message_type: "form" | "plain_text" | "unknown" | "winlink_form";
  } | null;
  form_view_html: string | null;
}
```

- [ ] **Step 3: Add the "Form view" block to `EditCheckinModal`**

In `frontend/src/pages/CheckInsPage.tsx`, find the existing "Original message" `<details>` block (added in Spec A, near line 439). Immediately AFTER it, add a second `<details>`:

```tsx
        {checkin?.form_view_html && (
          <details
            className="bg-bg-elevated/50 rounded-md border border-border"
            open
          >
            <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-text-secondary hover:text-text-primary">
              Form view
            </summary>
            <div className="px-3 pb-3">
              <iframe
                sandbox=""
                srcDoc={checkin.form_view_html}
                className="w-full h-96 border border-border rounded bg-white"
                title="Winlink form view"
              />
            </div>
          </details>
        )}
```

The `sandbox=""` attribute (empty value = maximally restrictive) blocks scripts, same-origin, top navigation, and forms; the rendered HTML also embeds its own CSP meta tag for defense in depth.

- [ ] **Step 4: Create `frontend/src/api/forms.ts`**

```typescript
import { apiFetch } from "./client";

export interface FormsStatus {
  source_url: string;
  library_version: string | null;
  last_fetched_at: string | null;
}

export interface FormsFetchResult {
  library_version: string;
  last_fetched_at: string;
}

export async function getFormsStatus(): Promise<FormsStatus> {
  return apiFetch("/api/config/forms/status");
}

export async function fetchFormsLibrary(): Promise<FormsFetchResult> {
  return apiFetch("/api/config/forms/fetch", { method: "POST" });
}
```

If `apiFetch` lives at a different path or has a different signature, check `frontend/src/api/client.ts` and adjust.

- [ ] **Step 5: Add the admin section to the config page**

In the config page identified in step 1, add a new section that loads `getFormsStatus()` on mount and shows:

- Current `library_version` (or "Not downloaded" if null)
- Current `last_fetched_at` (or "—" if null)
- A "Fetch latest" button that calls `fetchFormsLibrary()`, shows a loading state, and refreshes status on success or shows a toast on failure
- A tooltip on the button citing `status.source_url` so the admin sees where the download originates

Pattern this after an existing similar section (e.g., callbook config or SMTP test) — use whatever modal/toast/button components the page already uses.

- [ ] **Step 6: Type-check + build the frontend**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```

Expected: clean build, no type errors.

If a separate type-check is needed:

```bash
cd frontend && nix-shell -p nodejs_22 --run "npx tsc --noEmit"
```

Expected: no errors.

- [ ] **Step 7: Smoke test deferred to user**

This task adds a sandboxed iframe and an admin button. Manual visual verification (does the iframe render the form correctly? does the button trigger a fetch?) is on the user's test server, not in this session.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/forms.ts frontend/src/pages/CheckInsPage.tsx frontend/src/pages/AppConfigPage.tsx
# Adjust the config-page path in `git add` to match what step 1 found.
git commit -m "$(cat <<'EOF'
feat(forms): edit-modal form view iframe + admin "Fetch latest" UI

EditCheckinModal renders a sandboxed iframe of the server-supplied
form_view_html when present (always for winlink_form check-ins after
the backend either renders the template or falls back to a key-value
table). Config page gains a Winlink Standard Forms section showing
library_version + last_fetched_at and a Fetch latest button that
hits the admin endpoint and refreshes the status on success.

Manual visual smoke testing deferred to the user's test server.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Final verification

**Files:** none.

- [ ] **Step 1: Run the full backend suite, lint, and frontend build one last time**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
cd frontend && nix-shell -p nodejs_22 --run "npm run build" && cd ..
```

Expected: all green.

- [ ] **Step 2: Confirm the commit log on the feature branch**

```bash
git log --oneline main..HEAD
```

Expected: 7 commits in this order (oldest first):

```
<sha> feat(config): add state_dir setting for runtime-writable storage
<sha> feat(checkins): add WINLINK_FORM MessageType enum value
<sha> feat(checkins): parse <RMS_Express_Form> Winlink form check-ins
<sha> feat(forms): server-side renderer for Winlink form templates
<sha> feat(forms): admin endpoint to fetch the Standard Forms library
<sha> feat(checkins): serve form_view_html for winlink_form check-ins
<sha> feat(forms): edit-modal form view iframe + admin "Fetch latest" UI
```

- [ ] **Step 3: Hand off to the user**

Report:
- Branch is `feat/winlink-forms`, 7 commits.
- All tests + lint + frontend build green.
- Smoke testing pending on the user's test server (forms-library download + form rendering require the deployed environment).
- Once smoke-test passes, integrate per the repo convention (default local merge to `main` for solo project; PR if review is wanted).
