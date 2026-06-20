# Check-in parser fix

**Status:** Draft
**Author:** Ben Kuhn (with Claude)
**Date:** 2026-06-19

## Problem

The check-in parser mis-parses most real messages from the user's net. Three coupled
bugs surfaced from a single example body:

```
Ben, KU0HN, Lewiston, Winona, MN, VHF Packet via KU0HN-10
```

1. **Whitespace tokenization.** `parse_plain_text_message` in
   `backend/modules/checkins/message_parser.py` splits the post-callsign body on
   whitespace and assumes each location field is one token. Real messages use
   commas as field delimiters and contain multi-word place names ("Grand Island",
   "Winona County"). For the example above, the parser produces
   `city=","`, `county="Lewiston,"`, `state="Winona,"`, and the table renders
   `",, Winona,"` because `[c.city, c.state].filter(Boolean).join(", ")` keeps
   the stray comma.

2. **Single-token mode matching.** `DEFAULT_KNOWN_MODES` is a set of single
   words. The default `checkins.modes` AppConfig list already contains
   multi-word modes ("VARA FM", "1200-baud Packet"), but the parser matches one
   whitespace-token at a time, so "VHF Packet" can never resolve as a unit.

3. **Gateway callsign hijacking.** `re.search` picks the *first* callsign-shaped
   match in the body. For a relayed message whose own callsign sits elsewhere
   but ends in `via KU0HN-10`, the parser can attribute the check-in to the
   gateway operator.

In addition, **PAT mailbox files are deleted at import time** (commit 02ddc76),
which eliminates the ability to recover from a parser bug by re-running the
import. The user encountered exactly this: discovered the parsing bug only
after the source files were gone.

## Goals

- Parse the user's actual message format (`Name, Callsign, City, County, State,
  Mode comments`) correctly.
- Stop mis-attributing check-ins to gateway operators.
- Give the NCO a one-screen path to hand-fix any check-in that the parser still
  gets wrong: read the original message body in the same modal where they edit
  the parsed fields.
- Preserve the source files long enough that a future parser fix could re-run
  against the originals — at minimum, until the roster for that session has
  been sent.

## Non-goals

- **No new configuration knobs.** The format is hardcoded to what the user's
  net sends. Other deployments that need a different shape can change the
  parser or add config in a follow-up.
- **No re-parse action.** RawMessage.body is preserved in the DB, so a future
  "Re-parse with current parser" button is feasible, but not built here. NCOs
  use the edit modal to hand-fix existing bad rows.
- **No Winlink Standard Forms support.** Free-form `Location:` fields and
  unfamiliar template field names still hit manual_review. The companion spec
  `2026-06-19-winlink-forms-design.md` covers that work.

## Design

### Parser rewrite (`backend/modules/checkins/message_parser.py`)

Detection in `detect_message_type` stays as it is: ≥3 known `Field: value`
lines → FORM, else if a callsign regex matches → PLAIN_TEXT, else UNKNOWN.

`parse_form_message` is unchanged.

`parse_plain_text_message` is rewritten:

1. Split the stripped body on commas. Trim each segment.
2. If there are ≥4 segments AND segment 1 matches the callsign regex
   (`\b[A-Z]{1,2}\d[A-Z]{1,3}\b`), treat as the structured comma form:
   - **segment 0** → `name`
   - **segment 1** → `callsign` (uppercased; trailing `-NN` tactical suffix
     stripped, e.g. `KU0HN-10` → `KU0HN`)
   - **middle segments** (between callsign and the trailing mode segment) →
     location, mapped by count:
     - 3 segments → `city`, `county`, `state`
     - 2 segments → `city`, `state`
     - 1 segment → `city`
     - 0 segments → all None
   - **last segment** → mode + comments. Iterate the known modes sorted
     longest-first; the mode matches if the segment starts with that mode
     followed by either end-of-string or whitespace (case-insensitive). So
     `"VHF Packet"` wins over `"Packet"` when both are configured, and
     `"Packetone"` doesn't accidentally match `"Packet"`. The mode value
     stored is the canonical form from the modes list (preserves operator
     casing). The remainder after the mode (lstripped) is `comments` (None
     if empty). If no known mode matches, the whole segment is `comments`,
     `mode` stays `""`, and confidence drops to low → manual_review.
   - `confidence`: `medium` when callsign + name + a matched mode are all
     present; `low` otherwise.
3. If the body has no commas (fewer than 2 segments) or segment 1 isn't a
   callsign, fall back to a degraded extract:
   - Scan the body for callsign matches, but skip any match preceded by
     `via ` (case-insensitive, with optional whitespace) so gateway suffixes
     don't win.
   - If a callsign is found, populate just `callsign`; all other fields None,
     `mode=""`, `comments=None`, `confidence="low"` → manual_review.
   - If no callsign is found, return the all-blank low-confidence dict (same
     shape as today's "unparseable").

The "VHF Packet via KU0HN-10" tail is handled because the callsign is anchored
at segment 1 of the comma split. The gateway suffix lives inside the comments
of the mode segment and never competes for the callsign slot.

The `known_modes` parameter is still passed in by the caller
(`process_raw_message` in `service.py` reads `checkins.modes` from AppConfig);
its semantics change from "set of single tokens" to "list of full mode
strings, longest-first matching."

### Edit modal raw-body display

`EditCheckinModal` in `frontend/src/pages/CheckInsPage.tsx` currently shows
only the parsed fields. Changes:

- **API:** the per-check-in serializer (and the list serializer if it doesn't
  blow up the payload — current list is small) gains an optional
  `raw_message` field:

  ```ts
  raw_message?: {
    subject: string;
    from_address: string;
    received_at: string;
    body: string;
  } | null;
  ```

  Sourced via the existing `CheckIn.raw_message` relationship. Null for
  manually-created check-ins.

- **Modal:** above the form fields, render a `<details>` block titled
  "Original message" containing subject / from / received timestamp and the
  body in a scrollable `<pre>` (monospace, max-height with overflow). Defaults
  to `open` when `parse_status === "manual_review"`, collapsed otherwise.
  Suppressed entirely when `raw_message` is null.

### Defer mailbox deletion to roster-sent

1. **Schema:** add a nullable column to `RawMessage`:

   ```python
   source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
   ```

   Alembic migration `add_raw_message_source_path` adds the column with NULL
   default; no backfill (the files those rows came from are already gone per
   the current behavior of commit 02ddc76).

2. **Import (`scan_and_import_messages` in
   `backend/modules/checkins/service.py`):** stop calling `_purge_source_files`.
   Persist the on-disk path onto each new `RawMessage`. For the dedupe branch
   (message already exists in DB from a prior scan), upsert `source_path` if
   currently NULL so a re-scan can recover the link.

3. **Roster send (`mark_sent` in `backend/modules/roster/service.py`):** after
   the successful delivery commit (`log.status = SENT; db.commit()`), look up
   every `RawMessage` joined through `CheckIn.session_id == log.session_id`,
   collect non-null `source_path` values, and unlink them. Failures
   `logger.warning` and continue — never abort or roll back the send.

4. **Roster skip (`skip_roster`):** same purge call. A skipped roster is an
   explicit decision not to publish; source files have no remaining value.

5. **Shared utility:** move `_purge_source_files` to a small module-level
   helper that takes a list of paths (not a list of "message dicts" as today).
   Both `scan_and_import_messages` (for future use if we ever want to purge
   inline again) and the roster module call it through this single entry
   point.

## Edge cases

- **Message with only callsign and no commas.** Falls through to the
  degraded extract; populates callsign only, marks manual_review. NCO sees
  the raw body in the modal and fills the rest.
- **Message with the old whitespace format** (`John W0ABC Denver CO Winlink`).
  No commas, so it lands in the degraded extract → callsign-only,
  manual_review. Acceptable: the user's net uses commas and this is a known
  trade-off.
- **Multi-word place names containing internal commas** (rare; e.g., "City,
  Inc.") will mis-split. Out of scope; the NCO fixes in the edit modal.
- **Roster sent → undo?** No undo path exists today. Once sent, files are
  gone. If a re-send action is added later, it just runs against the DB rows;
  source files aren't needed.
- **`skip_roster` then later "actually send"?** Not currently possible
  (skip is terminal). If that changes, source files will already be gone for
  skipped sessions — accept this; re-parsing isn't a workflow for skipped
  rosters.
- **Existing bad check-ins from prior scans.** Not auto-fixed. NCO opens the
  edit modal, reads the raw body (preserved in `RawMessage.body` regardless
  of file deletion), and corrects the fields by hand. Source files for these
  rows are already gone (per pre-change behavior), so no path-based recovery.

## Testing

Extend `tests/test_message_parser.py`:

- The "Ben, KU0HN, Lewiston, Winona, MN, VHF Packet via KU0HN-10" example
  parses to `name="Ben"`, `callsign="KU0HN"`, `city="Lewiston"`,
  `county="Winona"`, `state="MN"`, `mode="VHF Packet"`,
  `comments="via KU0HN-10"`, `confidence="medium"`.
- Comma form with 5 segments (no county) maps to city + state.
- Comma form with `KU0HN-10` in segment 1 strips the suffix.
- No-comma message with a callsign and a `via XXXXX-NN` tail extracts the
  *primary* callsign, not the gateway.
- Multi-word mode beats single-word when both are configured (e.g., "VARA HF"
  matches before "VARA" if both are in the list).
- Whitespace-only legacy format degrades to callsign-only manual_review.
- Unparseable body returns the all-blank low-confidence dict.

Extend `tests/test_checkin_service.py`:

- `scan_and_import_messages` no longer deletes source files at import time.
- `RawMessage.source_path` is populated for new imports and upserted on
  re-scan dedupe.

New `tests/test_roster_service.py` cases (or extend existing):

- `mark_sent` triggers deletion of source files associated with the session's
  check-ins.
- `skip_roster` triggers the same deletion.
- Missing files / read-only paths log a warning but don't fail the send.

Frontend: existing `CheckInsPage` tests get one case for the raw-message
`<details>` block (presence, default-open when manual_review).

## Migration

One Alembic migration: add `raw_messages.source_path` (nullable VARCHAR(1024)).
No data backfill required.

## Rollout

- Single PR / single branch.
- After merge, the next PAT mailbox scan will populate `source_path` for new
  arrivals. Historical RawMessages stay NULL — they're orphaned from a
  deletion standpoint, which is fine because their files are already gone.
- The next roster `mark_sent` after that scan will be the first one that
  actually purges files. Until then, the mailbox holds the files indefinitely
  (the deployment can handle this; it's already configured for read/write
  via `ReadWritePaths`).
