# Winlink Standard Forms support (receive + render only)

**Status:** Draft
**Author:** Ben Kuhn (with Claude)
**Date:** 2026-06-19

## Problem

Many net members send check-ins via Winlink Express form templates (e.g.,
"Winlink Check-in"). The transmitted body is wrapped in an
`<RMS_Express_Form>` XML structure, not the comma-delimited plain text or
simple `Field: value` form that
[Spec 2026-06-19-checkin-parser-fix](./2026-06-19-checkin-parser-fix-design.md)
handles. Today these messages are recognized as plain-text by the existing
parser (the `<…>` body has no recognized `Field: value` lines and contains
many callsign-shaped tokens), so they fall through to the degraded path and
produce garbage check-ins.

In addition, when an NCO opens the edit modal to hand-fix a form-derived
check-in, the only display today (after Spec A lands) is the raw XML body —
which is unreadable without parsing it mentally. The NCO needs to see the
form the way the sender saw it in Winlink Express.

## Goals

- Recognize Winlink Express form messages and extract check-in fields from
  their `<variables>` block.
- Tolerate variation in variable naming across templates via a
  heuristic-first / per-template-override field mapping.
- Re-parse a form's `comments` variable with Spec A's comma-parser to recover
  data that members typed into the comments box.
- Render the original form (read-only, scripts disabled) in the edit modal
  so the NCO can visually compare what was sent against the parsed fields.
- Distribute the Winlink Standard Forms HTML library via an admin-triggered
  runtime download, not a build-time bundle.

## Non-goals

- **No form authoring.** This spec covers receive + parse + render only.
  Activities that *send* forms, or any UI that lets a user fill and submit
  a form, is out of scope.
- **No script execution in rendered forms.** Templates that rely on JS for
  layout will degrade gracefully to a key-value table view.
- **No build-time bundling.** Operator clicks an admin button to download
  the library; the OCI image and NixOS module ship without it.
- **No automatic upstream sync.** The download is admin-triggered. No
  background refresh, no startup pull.
- **No support for non-check-in form types.** ICS-213, weather reports,
  position reports, etc. all get parsed into the same check-in field shape
  via the heuristic, but no specialized handling per form type.

## Design

### MessageType extension

Add `MessageType.WINLINK_FORM = "winlink_form"` to
`backend/modules/checkins/models.py`. Existing `MessageType.FORM` keeps its
meaning ("simple Field: value text").

`detect_message_type(body)` in `backend/modules/checkins/message_parser.py`
gets a new first branch: a case-insensitive substring check for
`<RMS_Express_Form>` in the body. Hit → `WINLINK_FORM`. Miss → existing
FORM/PLAIN_TEXT/UNKNOWN logic.

The substring check is cheap and reliable: every Winlink Express form
message contains this root element by construction. We don't parse XML to
detect.

### `parse_winlink_form_message(body, known_modes)`

1. **Parse XML defensively** with `xml.etree.ElementTree.fromstring`.
   - On `ParseError`: log a warning and fall through to
     `parse_plain_text_message` on the body so we never silently drop a
     message. The result inherits its confidence from that path.
2. **Extract template name:** read `form_parameters/display_form` text.
   Stored as `template_filename` for the rendering layer; not used in field
   mapping unless an override exists for it.
3. **Extract variables:** all `variables/var` elements as a
   `dict[str, str]` keyed by the `name` attribute (lowercased). Empty values
   stay as empty strings, not None.
4. **Apply field mapping** in two passes:

   - **Override pass** (lookup is case-insensitive on the filename) if
     `template_filename` is in `TEMPLATE_OVERRIDES`:
     The overrides map is a small dict in code:

     ```python
     TEMPLATE_OVERRIDES: dict[str, dict[str, str]] = {
         "winlink_check_in.html": {
             "callsign": "callsign",
             "name": "operator",
             "city": "city",
             ...
         },
         # Add more as templates with quirky names are encountered.
     }
     ```

     Each value is the variable name to read from the parsed dict for that
     check-in field. Override values win where present.

   - **Heuristic pass** for any check-in field still unset. Substring match
     against variable names (case-insensitive), first match wins. Patterns:

     | CheckIn field | Variable name substrings (first one that matches wins) |
     |---|---|
     | `callsign` | `callsign`, `call`, `station` |
     | `name` | `name`, `operator` |
     | `city` | `city` |
     | `county` | `county`, `parish`, `borough` |
     | `state` | `state`, `province` |
     | `mode` | `mode`, `modeofcheckin`, `check-in mode` |
     | `comments` | `comments`, `comment`, `notes`, `message` |
     | `latitude` | `latitude`, `lat` (parsed as float; ValueError → None) |
     | `longitude` | `longitude`, `long`, `lon` (parsed as float; ValueError → None) |

     "Substring match" means: does the variable name *contain* this
     substring as a sequence of characters (case-insensitive)? E.g.,
     variable `Senders_Callsign` matches `callsign`. Patterns are
     ordered most-specific-first to reduce false positives (`latitude`
     before `lat` so `latitude_text` doesn't end up in `lat` and lose
     the value to a less-specific match).

     Plus a combined-location fallback: if `city`/`county`/`state` are all
     unset after the above and any variable name matches `location` or
     `qth`, comma-split that value into 1/2/3 fields and assign to
     `city`/`state` or `city`/`county`/`state` by count (same rules as
     Spec A's plain-text middle-segment logic).

5. **Comments re-parse.** If `comments` is non-empty and any of
   `name/callsign/city/county/state/mode` are still missing, run Spec A's
   `parse_plain_text_message(comments, known_modes)`. For each field still
   unset, take the value from the plain-text parse. The structured form
   data always wins where both are present; the comments parser only fills
   gaps.

6. **Confidence:**
   - `high` if `callsign`, `name`, and `mode` are all set from the
     structured form (override + heuristic) without needing comments
     re-parse.
   - `medium` if those three are present but at least one came from the
     comments re-parse.
   - `low` otherwise.

7. **Return shape** identical to `parse_form_message` /
   `parse_plain_text_message` (the dict with `name`, `callsign`, `city`,
   `county`, `state`, `mode`, `comments`, `latitude`, `longitude`,
   `confidence`). The dispatcher in `parse_message` adds the new branch.

### Standard Forms library distribution

Runtime download into `${STATEDIR}/forms/`. No build-time bundling.

**New AppConfig keys** (both tunable via the admin config page):

- `forms.source_url` — defaults to
  `https://downloads.winlink.org/User%20Programs/Standard_Forms.zip`.
  Stored so the operator can point at a mirror if needed.
- `forms.library_version` — set by the download action; reads as
  `version|<sha256-prefix>` derived from the downloaded zip's filename
  (Winlink versions their zip e.g.
  `Standard_Forms_1.0.246.0.zip`; if the URL serves a generic filename,
  fall back to the first 12 hex chars of the file's SHA-256).
- `forms.last_fetched_at` — ISO-8601 timestamp written on each successful
  download.

**Backend endpoint** `POST /api/config/forms/fetch` (admin-only):

1. Resolve `forms.source_url` from AppConfig.
2. Issue a GET through the existing SSRF-guarded HTTP client (see commits
   3108db0, 870fe55) with a generous timeout (60s) and a max response
   size cap (50 MB; current Standard_Forms zip is ~8 MB).
3. Validate the response is a ZIP (magic bytes `PK\x03\x04`). Reject
   otherwise.
4. Extract to a temp directory:
   - Enforce a max entry count (5,000) and max uncompressed size (200 MB)
     to bound zip-bomb risk.
   - Filter included entries: only `.html`, `.txt`, `.xml`, `.css`. All
     other extensions (esp. `.js`, `.exe`, `.dll`) are dropped silently.
   - Reject any entry whose normalized path escapes the destination
     (zip-slip guard).
5. On success: atomically rename the temp dir over `${STATEDIR}/forms/`
   (or, if `forms/` is non-empty, rename it to `forms.old` then promote
   the new dir then `rm -rf forms.old`). Update `forms.library_version`
   and `forms.last_fetched_at` in AppConfig.
6. On any failure: leave existing `forms/` untouched, surface the error to
   the admin UI.

**Admin UI** (config page): "Winlink Standard Forms" section with the
current `library_version` and `last_fetched_at` displayed, plus a
"Fetch latest" button that hits the endpoint above. Tooltip on the button
notes the source URL so the admin knows where the download originates.

### Rendering received forms in the edit modal

Server-side rendering — frontend never does template substitution.

**Backend:** when the check-in detail/list serializer (already extended in
Spec A to include the joined `raw_message`) builds a payload for a check-in
whose `raw_message.message_type == "winlink_form"`, it also tries to
produce a `form_view_html` field:

1. Re-parse the `<RMS_Express_Form>` XML to recover `display_form` and
   the variables (this is cheap; alternatively cache the parsed result
   from the original `process_raw_message` call). On parse error,
   `form_view_html = None`.
2. Look up `${STATEDIR}/forms/Standard Forms/.../{display_form}` (the zip
   has a nested directory layout; we walk it once at startup and cache a
   `{filename → on-disk path}` map). If missing → `form_view_html = None`.
3. Read the template HTML. Apply the substitution syntax used by Winlink
   Express templates (`{var_name}` and/or `<input … name="var_name">`
   value injection — exact syntax to be confirmed against the zip during
   implementation; if templates use a non-trivial substitution language we
   degrade by returning `None` for that template so the UI falls back to
   the key-value table).
4. Pass the resulting HTML through `bleach` or an equivalent sanitizer
   configured to allow common form/structural tags (`div`, `span`, `p`,
   `h1`–`h6`, `table`, `tr`, `td`, `th`, `dl`, `dt`, `dd`, `ul`, `ol`,
   `li`, `input` (readonly), `label`, `br`, `hr`, `b`, `i`, `u`, `strong`,
   `em`, `style`-inline) and strip everything else including `<script>`,
   `<iframe>`, event handlers, `javascript:` URLs.
5. Return the sanitized HTML as `form_view_html`. Null when any step
   above fails or the library isn't downloaded.

**Frontend:** `EditCheckinModal` already renders the raw-body `<details>`
block (Spec A). If `form_view_html` is present, render a second
`<details>` titled "Form view" containing:

```jsx
<iframe
  sandbox=""                      // empty = max-restrictive
  srcDoc={form_view_html}
  className="w-full h-96 border border-border rounded"
  title="Winlink form view"
/>
```

If `form_view_html` is null but `message_type === "winlink_form"`, render a
`<dl>` of variable names → values instead (extracted from the
already-present raw XML body) as the "Form view" so the NCO at minimum sees
the structured fields.

**CSP / defense in depth.** The iframe's `sandbox=""` already blocks scripts
and same-origin. Add a `Content-Security-Policy` meta tag at the top of
the `srcDoc` (`default-src 'none'; style-src 'unsafe-inline'; img-src data:`)
so even if `sandbox=""` is misinterpreted by some browser, scripts stay
blocked. The main app's existing CSP (commit 870fe55) further constrains
the parent context.

### Service / API wire-up

- `process_raw_message` dispatches on the new message type and stores the
  field result the same way the existing paths do. No CheckIn model change.
- The CheckIn list endpoint adds `form_view_html` to its response shape
  (already adding `raw_message` per Spec A); both are optional/nullable to
  keep client compatibility tolerant.
- Form-view HTML is rendered lazily — only when a check-in detail or edit
  modal is opened. For the list view, we don't compute it. This keeps the
  list serializer fast.

## Edge cases

- **Library not downloaded.** All form parsing still works (heuristic +
  comments re-parse cover it). Rendering falls back to the key-value
  `<dl>`. Admin sees a banner on the config page prompting the download.
- **Template referenced by `display_form` is missing from the downloaded
  zip.** Same key-value table fallback.
- **`<RMS_Express_Form>` present but XML malformed.** Falls through to
  `parse_plain_text_message`. The `<details>` raw-body block in the modal
  shows the broken XML — operator can see it.
- **Member typed location into `comments` rather than separate fields.**
  Comments re-parse via Spec A handles it.
- **Member sent a non-check-in form** (e.g., ICS-213 General Message). The
  heuristic still extracts a callsign (`from` or `station` field), maybe
  a name. Confidence will be low → manual_review. NCO sees the form in
  the modal and either records the check-in or deletes the row.
- **Two `<RMS_Express_Form>` elements in one message body** (unlikely;
  forwarded form-within-form). Parse the first only.
- **Library download partially overwrites existing forms/ then crashes.**
  Mitigated by the temp-extract + atomic rename strategy; the existing
  `forms/` is never half-rewritten.

## Testing

`tests/test_message_parser.py`:

- A canonical `<RMS_Express_Form>` body with a Winlink Check-in template
  parses to all expected fields with `confidence=high`.
- Override map applied for a known template (override takes precedence
  over heuristic).
- Heuristic-only path for a template whose variable names match the
  patterns.
- Combined-location variable (`<var name="location">Lewiston, Winona, MN</var>`)
  splits into city/county/state.
- Comments re-parse fills `mode` when not present as a structured field
  but appears in `comments`.
- Malformed XML inside `<RMS_Express_Form>` falls through to plain-text
  parser and doesn't raise.
- Non-form body with no XML wrapper is unchanged from Spec A.

`tests/test_forms_fetch.py` (new):

- Endpoint requires admin auth.
- Successful download updates `forms.library_version` and
  `forms.last_fetched_at`.
- Zip-bomb (oversize) is rejected.
- Zip-slip path (`../../etc/passwd`) is rejected.
- Script files (`.js`) in the zip are not written to disk.
- Failure leaves the prior `forms/` directory untouched.
- SSRF-blocked source URL (private IP, file://, etc.) is rejected by the
  HTTP client and surfaces to the response.

`tests/test_form_render.py` (new):

- Template substitution produces the expected HTML for a fixture
  template + variable set.
- Sanitizer strips `<script>`, event handlers, `javascript:` URLs.
- Missing template returns `None` (frontend falls back to `<dl>`).
- Empty/missing library returns `None`.

Frontend: existing `CheckInsPage` tests get one case asserting the iframe
renders with `sandbox=""` when `form_view_html` is present, and the `<dl>`
fallback renders otherwise.

## Migration

- One Alembic migration: extend the `MessageType` enum to add
  `WINLINK_FORM`. On SQLite this is a no-op (string enum); on PostgreSQL,
  `ALTER TYPE … ADD VALUE`.
- AppConfig keys are created on-demand by the admin action; no seed needed.
- No data backfill. Existing `MessageType.PLAIN_TEXT` rows that were
  actually Winlink forms stay mis-classified (their parsed fields are
  garbage). NCO uses the edit modal to fix them — and once Spec A's
  raw-body display lands, the NCO can read the original XML there. A
  future one-shot "re-classify and re-parse" admin action could clean
  these up; out of scope for this spec.

## Rollout

- Single branch.
- After merge: existing form-shaped messages still mis-parse until an
  admin downloads the forms library AND new form messages arrive. The
  parser itself works without the library, but rendering doesn't.
- No deployment changes required (download writes to existing
  `${STATEDIR}` which is already in `ReadWritePaths`).
