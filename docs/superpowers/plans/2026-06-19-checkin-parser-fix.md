# Check-in parser fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the check-in plain-text parser to correctly handle comma-delimited
location, multi-word modes, and gateway callsigns; surface the original message
body in the edit modal; defer PAT mailbox file deletion until the session's
roster is sent or skipped.

**Architecture:** Three coordinated changes against `backend/modules/checkins`
(parser rewrite, deferred deletion via a new `RawMessage.source_path` column),
`backend/modules/roster/service.py` (purge hook on send/skip), and
`frontend/src/pages/CheckInsPage.tsx` (raw-body `<details>` block in the edit
modal). Backed by an Alembic migration for the new column.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / Alembic / pytest on the
backend; React 18 / TypeScript / Vite / Tailwind on the frontend. NixOS dev
shell (`nix-shell --run "<cmd>"`); `.venv/bin/pytest` runs the backend suite.

## Global Constraints

- All Python commands run inside `.venv/` (created by `shell.nix`). Do not
  `pip install` into the host. Use `.venv/bin/pytest`, `.venv/bin/alembic`,
  `.venv/bin/python`. Lint with `nix-shell --run "ruff check"`.
- Ruff settings: `line-length 120`, `select = ["E", "F"]`. Production code
  must pass without per-file ignores; tests have permissive ignores already
  configured.
- Conventional Commits: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`,
  `test:`, with optional scope (e.g. `feat(checkins): ...`).
- Frontend tooling lives in `frontend/`; run via
  `cd frontend && nix-shell -p nodejs_22 --run "npm <cmd>"`.
- Lists in the UI: no pagination, client-side filter/sort.
- The spec for this plan is
  `docs/superpowers/specs/2026-06-19-checkin-parser-fix-design.md`; that
  document is the source of truth for behavior. This plan implements it.
- All work happens on a feature branch (created in Task 0). After each task
  ends with a commit, run `.venv/bin/pytest -q` and
  `nix-shell --run "ruff check"`; both must be green before moving on.

---

### Task 0: Branch + worktree setup

**Files:**
- None to create. Sets up the working environment.

**Interfaces:**
- Consumes: clean `main` checkout.
- Produces: a feature branch ready for the rest of the plan.

- [ ] **Step 1: Confirm main is clean and up to date**

```bash
cd /home/ku0hn/dev/SkyNetControl
git status
git pull --ff-only origin main
```

Expected: `working tree clean`, `Already up to date.` (or fast-forward only).

- [ ] **Step 2: Create the feature branch**

```bash
git checkout -b feat/checkin-parser-fix
```

Expected: `Switched to a new branch 'feat/checkin-parser-fix'`.

- [ ] **Step 3: Verify the venv is functional**

```bash
.venv/bin/pytest -q tests/test_message_parser.py
```

Expected: existing tests pass (the suite as it currently stands; the plan
intentionally leaves them mostly intact and adds new ones).

If `bad interpreter` or import errors appear, rebuild the venv:
```bash
rm -rf .venv && nix-shell --run :
```
then re-run the pytest command above.

No commit at the end of this task — the branch is the deliverable.

---

### Task 1: Rewrite `parse_plain_text_message` around comma-splitting

**Files:**
- Modify: `backend/modules/checkins/message_parser.py` (replace the body of
  `parse_plain_text_message` and helpers; `parse_form_message` and
  `detect_message_type` stay unchanged)
- Modify: `tests/test_message_parser.py` (add cases; update any case that
  relied on whitespace tokenization)

**Interfaces:**
- Consumes: nothing from earlier tasks (Task 0 only set up the branch).
- Produces:
  - `parse_plain_text_message(body: str, known_modes: set[str] | None = None) -> dict`
    returns the same dict shape it does today
    (`name`, `callsign`, `city`, `county`, `state`, `mode`, `comments`,
    `latitude`, `longitude`, `confidence`). Semantics now follow Spec A
    section "Parser rewrite". The `known_modes` parameter is still a set
    of full mode strings (caller already passes the AppConfig modes list).
  - `parse_message(body, known_modes=None) -> tuple[MessageType, dict]`
    unchanged — still dispatches on `detect_message_type` and calls the new
    plain-text parser.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_message_parser.py`. (Keep existing tests for now;
Step 5 prunes the ones that are no longer valid.)

```python
def test_parse_plain_text_comma_form_canonical():
    """The motivating example from the spec parses end-to-end."""
    body = "Ben, KU0HN, Lewiston, Winona, MN, VHF Packet via KU0HN-10"
    result = parse_plain_text_message(body, known_modes={"VHF Packet", "Packet", "Voice"})
    assert result["name"] == "Ben"
    assert result["callsign"] == "KU0HN"
    assert result["city"] == "Lewiston"
    assert result["county"] == "Winona"
    assert result["state"] == "MN"
    assert result["mode"] == "VHF Packet"
    assert result["comments"] == "via KU0HN-10"
    assert result["confidence"] == "medium"


def test_parse_plain_text_comma_form_no_county():
    """5 comma segments map to city + state (no county)."""
    body = "Alice, W0ABC, Denver, CO, Voice good signal"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["name"] == "Alice"
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["county"] is None
    assert result["state"] == "CO"
    assert result["mode"] == "Voice"
    assert result["comments"] == "good signal"


def test_parse_plain_text_callsign_tactical_suffix_stripped():
    body = "Ben, KU0HN-10, Lewiston, MN, Voice"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["callsign"] == "KU0HN"


def test_parse_plain_text_multiword_mode_beats_single_word():
    body = "Ben, KU0HN, Lewiston, MN, VARA HF testing"
    result = parse_plain_text_message(body, known_modes={"VARA", "VARA HF"})
    assert result["mode"] == "VARA HF"
    assert result["comments"] == "testing"


def test_parse_plain_text_unknown_mode_marks_low_confidence():
    body = "Ben, KU0HN, Lewiston, MN, SomethingWeird"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["mode"] == ""
    assert result["comments"] == "SomethingWeird"
    assert result["confidence"] == "low"


def test_parse_plain_text_no_commas_degraded_extracts_callsign():
    """Whitespace-only legacy format: extract just the callsign, low confidence."""
    body = "John W0ABC Denver CO Winlink all good"
    result = parse_plain_text_message(body, known_modes={"Winlink"})
    assert result["callsign"] == "W0ABC"
    assert result["name"] == ""
    assert result["city"] is None
    assert result["confidence"] == "low"


def test_parse_plain_text_no_commas_skips_via_gateway():
    """A `via XXXXX-NN` callsign at the end must NOT be picked as the primary."""
    body = "Status update from John W0ABC via KU0HN-10"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["callsign"] == "W0ABC"


def test_parse_plain_text_no_callsign_anywhere_returns_blank():
    body = "Just some text with no callsign"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["callsign"] == ""
    assert result["confidence"] == "low"


def test_parse_plain_text_mode_match_requires_word_boundary():
    """A mode like 'Packet' must not match inside 'Packetone'."""
    body = "Ben, KU0HN, Lewiston, MN, Packetone test"
    result = parse_plain_text_message(body, known_modes={"Packet"})
    assert result["mode"] == ""
    assert result["comments"] == "Packetone test"


def test_parse_plain_text_canonical_mode_casing_preserved():
    """The stored mode value uses the casing from the known_modes set."""
    body = "Ben, KU0HN, Lewiston, MN, vhf packet"
    result = parse_plain_text_message(body, known_modes={"VHF Packet"})
    assert result["mode"] == "VHF Packet"
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
.venv/bin/pytest -q tests/test_message_parser.py -k "comma_form_canonical or comma_form_no_county or tactical_suffix or multiword_mode or unknown_mode or no_commas or word_boundary or canonical_mode"
```

Expected: most/all listed tests FAIL (some on assertion errors, some
because the parser produces today's whitespace-tokenized garbage).

- [ ] **Step 3: Replace `parse_plain_text_message`**

In `backend/modules/checkins/message_parser.py`, replace the existing
`parse_plain_text_message` function (and the unused
`DEFAULT_KNOWN_MODES` constant — comma parsing doesn't need a default
because the caller always passes `known_modes`; keep an empty-set
fallback for direct test calls without `known_modes`) with:

```python
import re

# Pattern is unchanged — kept as a module-level constant.
CALLSIGN_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z]{1,3}\b", re.IGNORECASE)

# Strip a trailing tactical suffix like "-10" from a callsign.
_TACTICAL_SUFFIX_RE = re.compile(r"-\d{1,3}$")

# "via XYZ-NN" pattern that we must NOT pick as the primary callsign
# when degrading the no-comma path.
_VIA_PREFIX_RE = re.compile(r"\bvia\s+$", re.IGNORECASE)


def _normalize_callsign(token: str) -> str:
    return _TACTICAL_SUFFIX_RE.sub("", token.upper())


def _assign_location(parts: list[str]) -> tuple[str | None, str | None, str | None]:
    """Map 0-3 trimmed location segments to (city, county, state) by count."""
    if len(parts) >= 3:
        return parts[0] or None, parts[1] or None, parts[2] or None
    if len(parts) == 2:
        return parts[0] or None, None, parts[1] or None
    if len(parts) == 1:
        return parts[0] or None, None, None
    return None, None, None


def _match_known_mode(segment: str, known_modes: set[str]) -> tuple[str, str | None]:
    """Return (mode, comments) for the trailing segment.

    Iterates known modes longest-first; a mode matches when the segment
    starts with it followed by end-of-string or whitespace
    (case-insensitive). The stored mode value preserves the casing from
    the known_modes set. No match: ('', whole_segment_or_None).
    """
    if not segment:
        return "", None
    sorted_modes = sorted(known_modes, key=len, reverse=True)
    lowered = segment.lower()
    for mode in sorted_modes:
        m_lower = mode.lower()
        if not lowered.startswith(m_lower):
            continue
        tail = segment[len(mode):]
        if tail == "" or tail[0].isspace():
            comments = tail.strip() or None
            return mode, comments
    return "", segment.strip() or None


def _degraded_extract(body: str) -> dict:
    """No-comma fallback: extract a primary callsign, skipping `via XXXXX-NN`."""
    callsign = ""
    for m in CALLSIGN_RE.finditer(body):
        # Look at what's immediately before the match (up to 8 chars is plenty).
        prefix = body[max(0, m.start() - 8):m.start()]
        if _VIA_PREFIX_RE.search(prefix):
            continue
        callsign = _normalize_callsign(m.group())
        break
    return {
        "name": "",
        "callsign": callsign,
        "city": None,
        "county": None,
        "state": None,
        "mode": "",
        "comments": None,
        "latitude": None,
        "longitude": None,
        "confidence": "low",
    }


def parse_plain_text_message(body: str, known_modes: set[str] | None = None) -> dict:
    """Parse a plain-text check-in body.

    Primary format (comma-delimited):
        Name, Callsign, City[, County], State, Mode comments

    Anything else falls through to a degraded extract that pulls only the
    primary callsign (skipping `via XXXXX-NN` gateway suffixes).
    """
    if known_modes is None:
        known_modes = set()

    text = body.strip()
    if not text:
        return _degraded_extract(text)

    if "," not in text:
        return _degraded_extract(text)

    segments = [s.strip() for s in text.split(",")]
    # The primary path needs at least Name, Callsign, plus one location/mode segment
    # and a trailing mode segment — 4 segments minimum.
    if len(segments) < 4:
        return _degraded_extract(text)

    callsign_match = CALLSIGN_RE.fullmatch(segments[1])
    if callsign_match is None:
        return _degraded_extract(text)

    name = segments[0]
    callsign = _normalize_callsign(segments[1])
    location_segments = segments[2:-1]
    trailing = segments[-1]

    city, county, state = _assign_location(location_segments)
    mode, comments = _match_known_mode(trailing, known_modes)

    if callsign and name and mode:
        confidence = "medium"
    else:
        confidence = "low"

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
```

`parse_form_message`, `detect_message_type`, and `parse_message` stay
exactly as they are. The `FORM_FIELDS`, `REQUIRED_FORM_FIELDS`, and
`CALLSIGN_RE` module-level definitions remain (the rewrite uses
`CALLSIGN_RE`). Delete `DEFAULT_KNOWN_MODES` — it's no longer used.

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
.venv/bin/pytest -q tests/test_message_parser.py -k "comma_form_canonical or comma_form_no_county or tactical_suffix or multiword_mode or unknown_mode or no_commas or word_boundary or canonical_mode"
```

Expected: all listed tests PASS.

- [ ] **Step 5: Update old tests that relied on whitespace tokenization**

Run the full module to see what regressed:

```bash
.venv/bin/pytest -q tests/test_message_parser.py
```

The following existing tests in `tests/test_message_parser.py` will fail
because they used whitespace-separated bodies. Replace each with the
updated assertions below (in place; keep the function name so test
history is preserved).

`test_parse_plain_text_message`:

```python
def test_parse_plain_text_message():
    body = "John Smith, W0ABC, Denver, Denver, CO, Winlink All good here"
    result = parse_plain_text_message(body, known_modes={"Winlink"})
    assert result["name"] == "John Smith"
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["county"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Winlink"
    assert result["comments"] == "All good here"
    assert result["confidence"] == "medium"
```

`test_parse_plain_text_minimal`:

```python
def test_parse_plain_text_minimal():
    body = "John, W0ABC, Denver, CO, Winlink"
    result = parse_plain_text_message(body, known_modes={"Winlink"})
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Winlink"
    assert result["confidence"] == "medium"
```

`test_parse_plain_text_unparseable` — still valid, no change needed
(`"Hello"` is no-comma so degrades; `confidence == "low"` still holds).

`test_parse_message_dispatches_plain_text`:

```python
def test_parse_message_dispatches_plain_text():
    body = "John Smith, W0ABC, Denver, Denver, CO, Winlink"
    msg_type, fields = parse_message(body, known_modes={"Winlink"})
    assert msg_type == MessageType.PLAIN_TEXT
    assert fields["callsign"] == "W0ABC"
```

`test_parse_plain_text_custom_modes`:

```python
def test_parse_plain_text_custom_modes():
    """Parser uses custom known_modes when provided."""
    body = "John Smith, W0ABC, Denver, CO, VARA-FM Running well"
    result = parse_plain_text_message(body, known_modes={"VARA-FM"})
    assert result["mode"] == "VARA-FM"
    assert result["comments"] == "Running well"
```

Delete `test_parse_plain_text_default_modes_still_work` — there is no
default mode list anymore; the caller always passes `known_modes`.

- [ ] **Step 6: Run the full parser test module**

```bash
.venv/bin/pytest -q tests/test_message_parser.py
```

Expected: all tests PASS.

- [ ] **Step 7: Run the full backend suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green. If `test_checkin_service.py` or `test_checkin_modes.py`
fails because they construct bodies that the new parser handles
differently, update those bodies to use comma-delimited form following
the same pattern as the parser tests above. Don't change parser behavior
to accommodate them.

- [ ] **Step 8: Commit**

```bash
git add backend/modules/checkins/message_parser.py tests/test_message_parser.py
# Add any test files updated in step 7 for compatibility.
git status
git commit -m "$(cat <<'EOF'
fix(checkins): rewrite plain-text parser around comma-delimited format

The previous parser tokenized the post-callsign body on whitespace and
assumed each location field was one token. Real check-in messages from
this net look like
"Ben, KU0HN, Lewiston, Winona, MN, VHF Packet via KU0HN-10": the
location is comma-delimited, modes can be multi-word, and a "via
KU0HN-10" gateway suffix would hijack the callsign slot.

Rewritten to split on commas, anchor the callsign at segment 1, map the
middle segments to city/county/state by count, and match the trailing
mode against the configured modes list longest-first (with a word
boundary so "Packet" doesn't match "Packetone"). Tactical -NN suffixes
on the callsign are stripped. Bodies that don't fit the comma format
fall through to a degraded extract that pulls only the primary
callsign and explicitly skips "via XXXXX-NN" patterns.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Alembic migration — add `raw_messages.source_path`

**Files:**
- Create: `alembic/versions/<rev>_add_raw_message_source_path.py`
  (Alembic picks the revision id; the file lands in `alembic/versions/`)
- Modify: `backend/modules/checkins/models.py` (add the column to the
  `RawMessage` model)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `RawMessage.source_path: Mapped[str | None]` — the on-disk path of
    the PAT mailbox file this row was imported from. NULL for rows
    imported before this migration or for rows added manually.

- [ ] **Step 1: Add the column to the model**

In `backend/modules/checkins/models.py`, inside `class RawMessage`,
after the existing `parsed` column (around line 47), add:

```python
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
```

`String` is already imported in this file; no new imports needed.

- [ ] **Step 2: Generate the migration**

```bash
.venv/bin/alembic revision -m "add raw_message source_path"
```

Expected output: `Generating .../alembic/versions/<rev>_add_raw_message_source_path.py ... done`.

- [ ] **Step 3: Fill in the migration body**

Open the file created in Step 2. Replace the auto-generated `upgrade`
and `downgrade` stubs with:

```python
def upgrade() -> None:
    with op.batch_alter_table("raw_messages") as batch_op:
        batch_op.add_column(sa.Column("source_path", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("raw_messages") as batch_op:
        batch_op.drop_column("source_path")
```

`batch_alter_table` is required for SQLite ALTER compatibility — match
the pattern used in existing migrations
(`alembic/versions/064082225c20_rename_map_url_to_session_url.py`).

Confirm the `down_revision` at the top of the new file matches the
current head:

```bash
.venv/bin/alembic heads
```

Expected: head matches the `down_revision` value Alembic wrote into
the new migration. If they don't match, the new migration's
`down_revision` must be set to the existing head.

- [ ] **Step 4: Apply the migration locally**

```bash
.venv/bin/alembic upgrade head
```

Expected: `Running upgrade ... -> <rev>, add raw_message source_path`.

- [ ] **Step 5: Verify the column exists**

```bash
.venv/bin/python -c "from sqlalchemy import create_engine, inspect; e=create_engine('sqlite:///skynetcontrol.db'); print([c['name'] for c in inspect(e).get_columns('raw_messages')])"
```

Expected: output includes `'source_path'`.

- [ ] **Step 6: Run tests + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green. The new column is nullable with no code reading
it yet, so existing tests pass unchanged.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/*_add_raw_message_source_path.py backend/modules/checkins/models.py
git commit -m "$(cat <<'EOF'
feat(checkins): add raw_messages.source_path column

Nullable column will hold the on-disk PAT mailbox path that each
RawMessage was imported from, so deletion of source files can be
deferred until the session's roster is sent (instead of running at
import time and destroying the only recovery path after a parser bug).

Pre-existing rows stay NULL; their files are already gone per the
behavior of commit 02ddc76 and aren't recoverable anyway.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Persist `source_path` at import; stop deleting at import time

**Files:**
- Modify: `backend/modules/checkins/service.py`
  - Refactor `_purge_source_files(messages: list[dict])` →
    `purge_source_files(paths: Iterable[str])` (rename, move to module
    level for shared use, take a flat iterable of paths).
  - Add `purge_session_source_files(db: Session, session_id: int)` that
    looks up source paths joined through CheckIn → RawMessage and calls
    `purge_source_files`.
  - In `scan_and_import_messages`: persist `source_path` on the new
    `RawMessage`, upsert it for the dedupe branch, and remove the two
    existing `_purge_source_files(...)` calls.
- Modify: `tests/test_checkin_service.py` to assert the new behavior.

**Interfaces:**
- Consumes:
  - The `RawMessage.source_path` column from Task 2.
- Produces:
  - `purge_source_files(paths: Iterable[str]) -> None` — best-effort
    `os.unlink` of each path, logs warnings on `OSError` (other than
    `FileNotFoundError` which is swallowed). No return value, never
    raises.
  - `purge_session_source_files(db: Session, session_id: int) -> int` —
    looks up `RawMessage.source_path` for every `RawMessage` joined via
    `CheckIn.raw_message_id` for the given `session_id`, filters
    non-null, calls `purge_source_files`, and returns the number of
    paths it attempted to delete. (Return value is for tests + future
    logging; callers may ignore it.)

- [ ] **Step 1: Write the failing tests**

Open `tests/test_checkin_service.py` and add (or replace existing
purge-related tests with) the following. Imports at the top of the
file should include `purge_session_source_files` once it exists; for
the failing-first test step, write the test as if it does.

```python
def test_scan_and_import_persists_source_path(db_session, tmp_path):
    """New imports record their on-disk path on the RawMessage row."""
    from backend.modules.checkins.service import scan_and_import_messages
    from backend.modules.checkins.models import RawMessage
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=1,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.NET,
        grace_period_hours=24,
    )
    db_session.add(net_session)
    db_session.commit()
    db_session.refresh(net_session)

    src = tmp_path / "12345.b2f"
    src.write_text("placeholder")

    msg = {
        "message_id": "<unique-id-1@example>",
        "from_address": "w0abc@winlink.org",
        "received_at": datetime.now(tz=timezone.utc),
        "subject": "//WL2K Check-in",
        "body": "John, W0ABC, Denver, CO, Voice",
        "path": str(src),
    }
    scan_and_import_messages(db_session, [msg], net_session)

    row = db_session.query(RawMessage).filter_by(message_id="<unique-id-1@example>").one()
    assert row.source_path == str(src)
    # File should still be on disk (deletion is deferred to roster-send).
    assert src.exists()


def test_scan_and_import_upserts_source_path_on_rescan(db_session, tmp_path):
    """A rescan of the same message backfills source_path if it was NULL."""
    from backend.modules.checkins.service import scan_and_import_messages
    from backend.modules.checkins.models import RawMessage
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=1,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.NET,
        grace_period_hours=24,
    )
    db_session.add(net_session)
    db_session.commit()
    db_session.refresh(net_session)

    # Seed a RawMessage as if it were imported before the migration: no source_path.
    existing = RawMessage(
        message_id="<unique-id-2@example>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="//WL2K Check-in",
        body="John, W0ABC, Denver, CO, Voice",
        message_type=__import__("backend.modules.checkins.models", fromlist=["MessageType"]).MessageType.UNKNOWN,
        parsed=False,
        source_path=None,
    )
    db_session.add(existing)
    db_session.commit()

    src = tmp_path / "12346.b2f"
    src.write_text("placeholder")

    msg = {
        "message_id": "<unique-id-2@example>",
        "from_address": "w0abc@winlink.org",
        "received_at": datetime.now(tz=timezone.utc),
        "subject": "//WL2K Check-in",
        "body": "John, W0ABC, Denver, CO, Voice",
        "path": str(src),
    }
    scan_and_import_messages(db_session, [msg], net_session)

    row = db_session.query(RawMessage).filter_by(message_id="<unique-id-2@example>").one()
    assert row.source_path == str(src)
    assert src.exists(), "scan must NOT delete the file at import time"


def test_purge_session_source_files_deletes_all_paths(db_session, tmp_path):
    from backend.modules.checkins.service import purge_session_source_files
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=1,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.NET,
        grace_period_hours=24,
    )
    db_session.add(net_session)
    db_session.commit()
    db_session.refresh(net_session)

    paths = []
    for i in range(3):
        p = tmp_path / f"file{i}.b2f"
        p.write_text("x")
        paths.append(p)
        raw = RawMessage(
            message_id=f"<id-{i}@x>",
            from_address="w0abc@winlink.org",
            received_at=datetime.now(tz=timezone.utc),
            subject="s",
            body="b",
            message_type=MessageType.UNKNOWN,
            parsed=True,
            source_path=str(p),
        )
        db_session.add(raw)
        db_session.flush()
        ci = CheckIn(
            session_id=net_session.id,
            raw_message_id=raw.id,
            callsign="W0ABC",
            name="Test",
            mode="Voice",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        )
        db_session.add(ci)
    db_session.commit()

    deleted = purge_session_source_files(db_session, net_session.id)
    assert deleted == 3
    for p in paths:
        assert not p.exists()


def test_purge_session_source_files_skips_null_paths(db_session, tmp_path):
    """Rows with NULL source_path (pre-migration) are silently skipped."""
    from backend.modules.checkins.service import purge_session_source_files
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=1,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.NET,
        grace_period_hours=24,
    )
    db_session.add(net_session)
    db_session.commit()
    db_session.refresh(net_session)

    raw = RawMessage(
        message_id="<id@x>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="s",
        body="b",
        message_type=MessageType.UNKNOWN,
        parsed=True,
        source_path=None,
    )
    db_session.add(raw)
    db_session.flush()
    db_session.add(CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="Test",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))
    db_session.commit()

    assert purge_session_source_files(db_session, net_session.id) == 0


def test_purge_source_files_swallows_missing_file(tmp_path):
    """Missing files don't raise; other failures log a warning and continue."""
    from backend.modules.checkins.service import purge_source_files

    missing = tmp_path / "does-not-exist.b2f"
    real = tmp_path / "real.b2f"
    real.write_text("x")
    # Should not raise even though the first path is missing.
    purge_source_files([str(missing), str(real)])
    assert not real.exists()
```

If `tests/test_checkin_service.py` lacks a `db_session` fixture matching
the signature above, look for the existing fixture name in the file —
it should be the in-memory session fixture used by other tests in that
module — and substitute its name.

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
.venv/bin/pytest -q tests/test_checkin_service.py -k "source_path or purge_session or purge_source_files"
```

Expected: tests FAIL (function not defined / behavior not present).

- [ ] **Step 3: Refactor `_purge_source_files` and add helpers**

Edit `backend/modules/checkins/service.py`. Replace the existing
`_purge_source_files` function (lines 22-36 in the current file) with:

```python
from collections.abc import Iterable


def purge_source_files(paths: Iterable[str]) -> None:
    """Best-effort delete of PAT mailbox files. Missing files are silent;
    other OS errors log a warning but never raise.
    """
    for path in paths:
        if not path:
            continue
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Failed to delete mailbox file %s: %s", path, exc)


def purge_session_source_files(db: Session, session_id: int) -> int:
    """Delete the on-disk source files for all RawMessages attached to a
    session via its CheckIns. Returns the number of paths attempted.
    """
    rows = (
        db.query(RawMessage.source_path)
        .join(CheckIn, CheckIn.raw_message_id == RawMessage.id)
        .filter(CheckIn.session_id == session_id)
        .filter(RawMessage.source_path.isnot(None))
        .all()
    )
    paths = [row[0] for row in rows]
    purge_source_files(paths)
    return len(paths)
```

`Iterable` requires the import shown above; if `collections.abc` isn't
already imported in the file, add the line. `Session`, `RawMessage`,
`CheckIn`, `logger`, and `os` are already imported.

- [ ] **Step 4: Update `scan_and_import_messages` to persist + stop deleting**

In the same file, find `scan_and_import_messages` (begins around
line 121 in the current file). Two changes:

1. In the dedupe-only branch (where all messages already exist), delete
   the `_purge_source_files(already_imported)` call. Instead, upsert
   `source_path` for any pre-existing RawMessage whose `source_path` is
   currently NULL but for which the rescan has a path. Replace the
   `if not new_messages:` block:

   ```python
   if not new_messages:
       # All messages already in DB. Backfill source_path for any rows
       # that were imported before this field existed.
       _upsert_source_paths(db, already_imported)
       db.commit()
       return []
   ```

2. In the main branch where `RawMessage(...)` is constructed
   (currently around line 144), add `source_path=msg_dict.get("path")`
   to the keyword arguments. Then near the end of the function, where
   the existing code calls
   `_purge_source_files(new_messages + already_imported)`, replace that
   line with a backfill for already-imported rows only:

   ```python
   _upsert_source_paths(db, already_imported)
   db.commit()  # picks up both the new RawMessage source_paths (already flushed)
                # and any upserts
   ```

   Remove the existing standalone `db.commit()` immediately above the
   purge call if it duplicates this one. (There should be one
   `db.commit()` that flushes the new RawMessage + CheckIn rows + the
   upserts together.)

Add the helper alongside `purge_source_files`:

```python
def _upsert_source_paths(db: Session, message_dicts: list[dict]) -> None:
    """For each already-imported message dict that has a 'path', backfill
    RawMessage.source_path when currently NULL. No-op otherwise.
    """
    by_id = {m["message_id"]: m.get("path") for m in message_dicts if m.get("path")}
    if not by_id:
        return
    rows = db.query(RawMessage).filter(RawMessage.message_id.in_(by_id.keys())).all()
    for row in rows:
        if row.source_path is None:
            row.source_path = by_id[row.message_id]
```

- [ ] **Step 5: Run the new tests to verify they pass**

```bash
.venv/bin/pytest -q tests/test_checkin_service.py -k "source_path or purge_session or purge_source_files"
```

Expected: all listed tests PASS.

- [ ] **Step 6: Run the full backend suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green. If the existing
`test_scan_and_import_*_deletes_*` tests (added in commit 02ddc76) now
fail because they asserted source files were deleted at import time,
update those tests to assert the file *remains* on disk and that the
RawMessage row has its `source_path` populated. The behavior change is
intentional and documented in the spec.

- [ ] **Step 7: Commit**

```bash
git add backend/modules/checkins/service.py tests/test_checkin_service.py
git commit -m "$(cat <<'EOF'
refactor(checkins): defer PAT mailbox deletion; persist source_path

Imports now store the on-disk path on RawMessage.source_path instead
of deleting the file. Re-scans of already-imported messages backfill
the column when it's NULL, so files re-discovered after the migration
become eligible for deletion later.

New `purge_source_files(paths)` and `purge_session_source_files(db,
session_id)` helpers will be called from the roster module when a
session's roster is sent or skipped — that wiring follows in the next
commit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Purge source files on roster send / skip

**Files:**
- Modify: `backend/modules/roster/service.py` (call
  `purge_session_source_files` at the end of `mark_sent` and
  `skip_roster`)
- Modify: `tests/test_roster_service.py` (extend, or create if missing)

**Interfaces:**
- Consumes:
  - `purge_session_source_files(db, session_id)` from Task 3.
- Produces:
  - No new exports. Side-effect: a successful `mark_sent` or any
    `skip_roster` now deletes the on-disk PAT files for that session.

- [ ] **Step 1: Locate / create the test file**

```bash
ls tests/test_roster_service.py 2>/dev/null && echo "exists" || echo "missing"
```

If the file exists, append the tests below to it. If missing, create
`tests/test_roster_service.py` with the standard test header (look at
`tests/test_checkin_service.py` for the fixture imports the codebase
uses for db sessions).

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_roster_service.py`:

```python
def test_mark_sent_purges_session_source_files(db_session, tmp_path, monkeypatch):
    """A successful mark_sent deletes the session's PAT mailbox files."""
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from backend.modules.roster.service import mark_sent
    from backend.modules.roster.models import RosterLog, RosterStatus
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=1,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.NET,
        grace_period_hours=24,
    )
    db_session.add(net_session)
    db_session.commit()
    db_session.refresh(net_session)

    src = tmp_path / "file.b2f"
    src.write_text("x")
    raw = RawMessage(
        message_id="<id@x>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="s",
        body="b",
        message_type=MessageType.UNKNOWN,
        parsed=True,
        source_path=str(src),
    )
    db_session.add(raw)
    db_session.flush()
    db_session.add(CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="Test",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))

    log = RosterLog(
        session_id=net_session.id,
        status=RosterStatus.APPROVED,
        content_subject="s",
        content_header="h",
        content_welcome="w",
        content_comments="c",
        content_footer="f",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)

    # Stub the delivery dispatcher so the test doesn't try to send email.
    monkeypatch.setattr(
        "backend.integrations.delivery.service.dispatch_delivery",
        lambda *a, **kw: True,
    )

    result = mark_sent(db_session, log.id)
    assert result is not None
    assert result.status == RosterStatus.SENT
    assert not src.exists(), "source file must be purged after a successful send"


def test_mark_sent_failure_does_not_purge(db_session, tmp_path, monkeypatch):
    """A failed delivery (mark_sent returns None) leaves files in place."""
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from backend.modules.roster.service import mark_sent
    from backend.modules.roster.models import RosterLog, RosterStatus
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=1,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.NET,
        grace_period_hours=24,
    )
    db_session.add(net_session)
    db_session.commit()
    db_session.refresh(net_session)

    src = tmp_path / "file.b2f"
    src.write_text("x")
    raw = RawMessage(
        message_id="<id@x>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="s",
        body="b",
        message_type=MessageType.UNKNOWN,
        parsed=True,
        source_path=str(src),
    )
    db_session.add(raw)
    db_session.flush()
    db_session.add(CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="Test",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))

    log = RosterLog(
        session_id=net_session.id,
        status=RosterStatus.APPROVED,
        content_subject="s",
        content_header="h",
        content_welcome="w",
        content_comments="c",
        content_footer="f",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)

    monkeypatch.setattr(
        "backend.integrations.delivery.service.dispatch_delivery",
        lambda *a, **kw: False,
    )

    result = mark_sent(db_session, log.id)
    assert result is None
    assert src.exists(), "source files must remain when delivery failed"


def test_skip_roster_purges_session_source_files(db_session, tmp_path):
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from backend.modules.roster.service import skip_roster
    from backend.modules.roster.models import RosterLog, RosterStatus
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=1,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.NET,
        grace_period_hours=24,
    )
    db_session.add(net_session)
    db_session.commit()
    db_session.refresh(net_session)

    src = tmp_path / "file.b2f"
    src.write_text("x")
    raw = RawMessage(
        message_id="<id@x>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="s",
        body="b",
        message_type=MessageType.UNKNOWN,
        parsed=True,
        source_path=str(src),
    )
    db_session.add(raw)
    db_session.flush()
    db_session.add(CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="Test",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))

    log = RosterLog(
        session_id=net_session.id,
        status=RosterStatus.DRAFT,
        content_subject="s",
        content_header="h",
        content_welcome="w",
        content_comments="c",
        content_footer="f",
        drafted_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)

    result = skip_roster(db_session, log.id)
    assert result is not None
    assert result.status == RosterStatus.SKIPPED
    assert not src.exists()
```

- [ ] **Step 3: Run the new tests to verify they fail**

```bash
.venv/bin/pytest -q tests/test_roster_service.py -k "purges or does_not_purge"
```

Expected: tests FAIL (source files remain because no purge is wired up yet).

- [ ] **Step 4: Wire the purge calls into the roster service**

Edit `backend/modules/roster/service.py`.

Add to the imports section near line 12 (alongside the existing
`from backend.modules.checkins.models import CheckIn`):

```python
from backend.modules.checkins.service import purge_session_source_files
```

In `mark_sent` (around line 435), at the very end of the success path,
right after `db.refresh(log)` and before `return log`, insert:

```python
    purge_session_source_files(db, log.session_id)
```

The failure paths (`return None`) must NOT call the purge — files must
survive a failed delivery so the operator can retry.

In `skip_roster` (around line 470), at the very end after
`db.refresh(log)` and before `return log`, insert the same call:

```python
    purge_session_source_files(db, log.session_id)
```

- [ ] **Step 5: Run the new tests to verify they pass**

```bash
.venv/bin/pytest -q tests/test_roster_service.py -k "purges or does_not_purge"
```

Expected: all PASS.

- [ ] **Step 6: Run the full backend suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green.

- [ ] **Step 7: Commit**

```bash
git add backend/modules/roster/service.py tests/test_roster_service.py
git commit -m "$(cat <<'EOF'
feat(roster): purge session source files when roster is sent or skipped

When mark_sent succeeds (delivery confirmed and status flipped to SENT)
or skip_roster runs (operator explicitly chose not to publish), drop
the on-disk PAT mailbox files for every RawMessage attached to that
session via its CheckIns. Failed deliveries leave files in place so
the operator can retry.

This is the consumer of purge_session_source_files added in the
previous commit and the second half of moving deletion out of the
import path: files now live as long as the NCO might still want to
re-parse them.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Include `raw_message` in the check-in API response

**Files:**
- Modify: `backend/modules/checkins/routes.py` (extend `_checkin_to_response`
  and `_checkin_to_response_with_session` to include the joined raw
  message when present)
- Modify: `tests/test_checkin_routes.py` (add assertion on the field)

**Interfaces:**
- Consumes: `CheckIn.raw_message` SQLAlchemy relationship (already
  present at line 71 of `backend/modules/checkins/models.py`).
- Produces:
  - The JSON payload returned by every `/api/checkins/...` endpoint
    that uses `_checkin_to_response` (list, detail, update response,
    create response) now contains an optional key:

    ```json
    "raw_message": {
        "subject": "...",
        "from_address": "...",
        "received_at": "<ISO-8601>",
        "body": "..."
    }
    ```

    Or `"raw_message": null` for manually-created check-ins.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_checkin_routes.py` (look for existing tests that hit
the list endpoint to copy the auth/setup pattern; the test below uses
placeholder names — adjust `client`, `auth_headers`, and the seed
helper to match what the file already provides):

```python
def test_list_checkins_includes_raw_message(client, auth_headers, db_session, seeded_session_id):
    """The list response exposes the joined raw_message body for parser-derived rows."""
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from datetime import datetime, timezone

    raw = RawMessage(
        message_id="<raw-1@x>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="//WL2K Check-in",
        body="John, W0ABC, Denver, CO, Voice",
        message_type=MessageType.PLAIN_TEXT,
        parsed=True,
        source_path="/tmp/x.b2f",
    )
    db_session.add(raw)
    db_session.flush()
    db_session.add(CheckIn(
        session_id=seeded_session_id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="John",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))
    db_session.commit()

    resp = client.get(f"/api/checkins/by-session/{seeded_session_id}", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    parser_row = next(r for r in rows if r["callsign"] == "W0ABC")
    assert parser_row["raw_message"] is not None
    assert parser_row["raw_message"]["body"] == "John, W0ABC, Denver, CO, Voice"
    assert parser_row["raw_message"]["subject"] == "//WL2K Check-in"
    assert parser_row["raw_message"]["from_address"] == "w0abc@winlink.org"
    assert "received_at" in parser_row["raw_message"]


def test_list_checkins_raw_message_null_for_manual(client, auth_headers, db_session, seeded_session_id):
    from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus

    db_session.add(CheckIn(
        session_id=seeded_session_id,
        raw_message_id=None,
        callsign="W0XYZ",
        name="Hand-entered",
        mode="Voice",
        parse_status=ParseStatus.MANUALLY_ENTERED,
        timing_status=TimingStatus.ON_TIME,
    ))
    db_session.commit()

    resp = client.get(f"/api/checkins/by-session/{seeded_session_id}", headers=auth_headers)
    manual_row = next(r for r in resp.json() if r["callsign"] == "W0XYZ")
    assert manual_row["raw_message"] is None
```

If the test file uses different fixture names (`api_client`, `db`,
`session_id`, etc.), substitute them. The exact GET endpoint path can
be confirmed with:

```bash
grep -n "by-session\|by_session\|@checkins_router.get" backend/modules/checkins/routes.py
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
.venv/bin/pytest -q tests/test_checkin_routes.py -k "includes_raw_message or raw_message_null_for_manual"
```

Expected: FAIL — `raw_message` key missing from response.

- [ ] **Step 3: Extend the serializer**

In `backend/modules/checkins/routes.py`, replace
`_checkin_to_response` (lines 52-69) with:

```python
def _checkin_to_response(checkin: CheckIn) -> dict:
    raw = checkin.raw_message
    raw_payload: dict | None
    if raw is None:
        raw_payload = None
    else:
        raw_payload = {
            "subject": raw.subject,
            "from_address": raw.from_address,
            "received_at": raw.received_at.isoformat(),
            "body": raw.body,
        }
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
    }
```

`_checkin_to_response_with_session` (lines 82-85) already delegates to
`_checkin_to_response`, so it inherits the change for free.

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
.venv/bin/pytest -q tests/test_checkin_routes.py -k "includes_raw_message or raw_message_null_for_manual"
```

Expected: PASS.

- [ ] **Step 5: Run the full backend suite + lint**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check"
```

Expected: both green. If any existing route test snapshot diff'd on
the response shape, update it to include `"raw_message": null` for
the previously-manual rows.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/checkins/routes.py tests/test_checkin_routes.py
git commit -m "$(cat <<'EOF'
feat(checkins): expose raw_message on the check-in API response

The list/detail endpoints now include the joined raw_message
(subject, from_address, received_at, body) so the edit modal can show
the original mailbox content alongside the parsed fields. Manually-
created check-ins serialize raw_message as null.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Render raw body inside the EditCheckinModal

**Files:**
- Modify: `frontend/src/types/index.ts` (extend the `CheckIn` interface)
- Modify: `frontend/src/pages/CheckInsPage.tsx` (render a `<details>`
  block above the form fields)

**Interfaces:**
- Consumes:
  - `CheckIn.raw_message` from the backend response shape produced in
    Task 5.
- Produces:
  - No new exports. UI-only change.

- [ ] **Step 1: Extend the TypeScript type**

In `frontend/src/types/index.ts`, replace the `CheckIn` interface
(lines 113-129) with:

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
  } | null;
}
```

- [ ] **Step 2: Render the details block in EditCheckinModal**

In `frontend/src/pages/CheckInsPage.tsx`, find the
`EditCheckinModal` component's JSX (the `return (...)` around line
436). Inside the outer `<div className="flex flex-col gap-3">` and
ABOVE the first `<CallsignLookupField ... />`, insert:

```tsx
        {checkin?.raw_message && (
          <details
            className="bg-bg-elevated/50 rounded-md border border-border"
            open={checkin.parse_status === "manual_review"}
          >
            <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-text-secondary hover:text-text-primary">
              Original message
            </summary>
            <div className="px-3 pb-3 flex flex-col gap-2">
              <div className="text-xs text-text-muted">
                <div><span className="font-medium">Subject:</span> {checkin.raw_message.subject}</div>
                <div><span className="font-medium">From:</span> {checkin.raw_message.from_address}</div>
                <div><span className="font-medium">Received:</span> {new Date(checkin.raw_message.received_at).toLocaleString()}</div>
              </div>
              <pre className="text-xs font-mono whitespace-pre-wrap bg-bg-base/60 border border-border rounded p-2 max-h-64 overflow-auto text-text-primary">
                {checkin.raw_message.body}
              </pre>
            </div>
          </details>
        )}
```

Manually-entered check-ins have `raw_message: null` and skip the
block entirely.

- [ ] **Step 3: Type-check the frontend**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```

Expected: build succeeds (the build step runs `tsc`; type errors will
fail it). If the build script doesn't run `tsc`, also run:

```bash
cd frontend && nix-shell -p nodejs_22 --run "npx tsc --noEmit"
```

Expected: no type errors.

- [ ] **Step 4: Manually smoke-test in the dev server**

From the repo root:

```bash
./run-dev.sh
```

Then in a browser:

1. Open `http://localhost:5173/checkins`.
2. Find a check-in that originated from a parsed message (parse_status
   = "auto" or "manual_review"). Click the edit pencil.
3. Verify the "Original message" `<details>` block renders with the
   subject, from, received timestamp, and body. Verify it's open by
   default when `parse_status === "manual_review"` and closed
   otherwise.
4. Click "Add Check-in" to open the create modal — it shouldn't even
   try to render the block since it has no `checkin` prop.
5. Edit a manually-created check-in (parse_status =
   "manually_entered"). Verify the block is absent.

If no real data is available locally, seed a row by hand:

```bash
.venv/bin/python -c "
from backend.app import sessionmaker_for_engine
from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
# ... (or use the SQLite admin tool of your choice)
"
```

A simpler option: use the existing test fixtures via
`.venv/bin/pytest tests/test_checkin_routes.py::test_list_checkins_includes_raw_message -v -s`
to confirm the API shape, then trust the visual layer rendered above.

Document the manual outcome (works / what looked off) in the commit
body in Step 6.

- [ ] **Step 5: Stop the dev server**

```
Ctrl+C in the run-dev.sh terminal.
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/pages/CheckInsPage.tsx
git commit -m "$(cat <<'EOF'
feat(checkins): show original message body in the edit modal

When the API returns a joined raw_message, the EditCheckinModal now
renders a <details> block above the form fields with the subject,
from address, received timestamp, and the raw body in a scrollable
<pre>. Defaults to open for parse_status == manual_review so the NCO
can read the source while correcting the parsed fields. Manually-
entered check-ins (raw_message: null) skip the block.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Final verification

**Files:** none

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

Expected: 6 commits, in this order:

```
<sha> feat(checkins): show original message body in the edit modal
<sha> feat(checkins): expose raw_message on the check-in API response
<sha> feat(roster): purge session source files when roster is sent or skipped
<sha> refactor(checkins): defer PAT mailbox deletion; persist source_path
<sha> feat(checkins): add raw_messages.source_path column
<sha> fix(checkins): rewrite plain-text parser around comma-delimited format
```

- [ ] **Step 3: Hand off to the user**

Report:
- Branch is `feat/checkin-parser-fix`, 6 commits.
- All tests + lint + frontend build green.
- Ask whether to merge to `main` (per the repo convention, default to
  local merge — solo project, no PR needed) or open a PR for review.
- Remind the user that Spec B (Winlink Forms) is the next planned
  arc and has not been touched in this branch.
