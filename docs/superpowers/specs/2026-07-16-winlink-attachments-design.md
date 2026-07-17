# Winlink Attachments + Inbound Form Capture Design

**Date:** 2026-07-16
**Status:** Approved for planning

## Context

This is **SP4a**, the foundation half of the forms work (SP4 of live events). Forms
composition (the user-facing feature) decomposed into two specs because the whole was
~2–3× the size of prior sub-projects:

- **SP4a (this spec):** a bidirectional B2F attachment codec, inbound mailbox attachment
  extraction, and reliable capture of *received* Winlink forms (their template name,
  `reply_template`, and variables) — surfaced through the API. Independently valuable: it
  hardens the existing receive side (forms that arrive as attachments are captured today
  only if their XML happens to sit in the message body).
- **SP4b (next spec):** form composition + reply — catalog browse/search, sandboxed
  form-fill (the form's own JS runs in a locked-down iframe, PAT-style), the PAT
  template→message builder + `RMS_Express_Form` XML generation, outbound compose wired
  into the event message path, and reply-with-form (consumes SP4a's captured inbound data).

Prior decisions from the SP4 Q&A (bind both specs):

- Composition runs the form's real JS in a **sandboxed iframe** (`allow-scripts`, no
  `allow-same-origin`, no network; variables leave via `postMessage`). — SP4b.
- Catalog: **browsable folder tree + search**, as every Winlink client presents it. — SP4b.
- Composition is **event-scoped** (plugs into SP3's Messages panel, produces an outbound
  `EventMessage`). — SP4b.
- Forms travel as a **true B2F attachment** (`RMS_Express_Form_<name>.xml`), not
  body-embedded, so real Winlink clients render them. — this spec builds the codec.
- **Reply-with-form** is in scope (SP4b) — so received forms must be captured with their
  `reply_template` + variables. — this spec captures them.
- Builder is **PAT-faithful** (`<Var>`, auto-filled insertion tags, `{Ask}`/`{Select}`
  surfaced as fields, `Def:`). — SP4b.
- The newer **JSON form format** is a future enhancement (not built).

## Requirements (SP4a)

- Parse and build `.b2f` (FBB text-and-attachment layout) including attachments.
- `WinlinkBackend.send` refactored onto the codec with byte-identical output for the
  current body-only case; attachment sending becomes possible (first used in SP4b).
- Inbound mailbox reader captures attachments (`.b2f` via the codec; `.mime`/`.eml` via
  stdlib email) alongside the existing text body, without regressing body extraction.
- Attachments persisted (new `raw_message_attachments` table), backfilled on re-scan.
- Received forms captured reliably from either an `RMS_Express_Form_*.xml` attachment or
  the body; `display_form` / `reply_template` / variables extracted however the form
  arrived.
- Message API exposes an attachments summary, an attachment download route, and a compact
  received-form metadata block.
- Robust against malformed/hostile input; attachment capture is best-effort and never
  blocks message import.

## Architecture

New module `backend/integrations/winlink/b2f.py` — a pure, bidirectional codec, isolated
from the delivery and mailbox layers that consume it.

### The codec (`b2f.py`)

Pure functions, no I/O, exhaustively unit-testable.

```python
@dataclass
class B2FAttachment:
    filename: str
    content_type: str   # inferred from extension
    data: bytes

@dataclass
class B2FMessage:
    headers: dict[str, str]      # Mid, From, To, Subject, Date, Mbo, ...
    body: str
    attachments: list[B2FAttachment]

class B2FParseError(Exception): ...

def parse_b2f(raw: bytes) -> B2FMessage: ...

def build_b2f(*, message_id, from_addr, to_addr, subject, mbo, date,
              body, attachments: Sequence[B2FAttachment] = ()) -> bytes: ...
```

- **Read:** header block → `Body: <len>` section → successive `File: <len> <filename>`
  attachment sections (the FBB layout PAT/RMS Express write). Tolerates the header quirks
  the mailbox reader already handles (`Mid:` vs `Message-Id:`, PAT `YYYY/MM/DD HH:MM`
  date).
- **Write:** the inverse. The existing CR/LF header-injection guard
  (`_strip_b2f_header_chars`, currently in `backends/winlink.py`) moves into `build_b2f`.
- **Refactor with zero behavior change:** `WinlinkBackend.send` (hand-builds a body-only
  `.b2f` today) calls `build_b2f(..., attachments=())`; output must be byte-identical, the
  existing delivery tests are the guard.
- **Scope boundary:** the FBB text-and-attachment layout of already-decoded `.b2f` files
  on disk — NOT the compressed B2 binary transfer protocol (PAT deals with decoded files
  too).

### Data model

New table `raw_message_attachments`:

| Column | Notes |
|---|---|
| `id` | PK |
| `raw_message_id` | FK → raw_messages.id, cascade delete with parent |
| `filename` | attachment name (metadata only; never used as a filesystem path) |
| `content_type` | inferred from extension |
| `data` | LargeBinary (attachment bytes) |
| `created_at` | |

Small attachments in practice (form XML is a few KB). A per-attachment size cap and a
per-message total cap in the reader prevent a hostile file from exhausting storage.

### Inbound extraction

`read_message_file` (mailbox_reader) gains an `attachments` key in its returned dict:
`[{filename, content_type, data}]`.

- `.b2f` → `parse_b2f`.
- `.mime`/`.eml` → stdlib `email` walk over non-text parts.
- Text-body extraction unchanged (regression-safe); attachments purely additive.

`scan_and_import_messages` persists attachments into `raw_message_attachments` for each
newly-imported message, and backfills them on the already-imported path (mirroring the
existing `source_path` backfill) so a re-scan captures attachments for older messages.
A persistence failure is logged and swallowed — the message import still succeeds.

### Received-form capture

Helper `find_form_xml(attachments, body) -> str | None` returns the `<RMS_Express_Form>`
XML from either (a) an attachment named `RMS_Express_Form_*.xml`, or (b) the body (today's
only path) as fallback. `parse_winlink_form_message` is fed whichever is found, so
`display_form`, `reply_template`, and variables are captured however the form arrived.

## API surface

Additive to SP3's message payloads/routes.

- **Attachments summary** on the inbound `EventMessage` response:
  `attachments: [{id, filename, content_type, size}]` (no bytes inline). The Messages
  panel shows a 📎 indicator.
- **Download route:** `GET /api/nets/{slug}/events/{id}/messages/{message_id}/attachments/{attachment_id}`
  (viewer). Streams the attachment bytes, scoped to event/net (404 cross-net). Served with
  a sanitized `Content-Disposition` filename and `Content-Type: application/octet-stream`
  (never the claimed type) so a hostile form XML downloads, never renders.
- **Received-form metadata** on the inbound message response: `form: {display_form,
  reply_template, is_form: true}` for messages carrying a form (computed on read via
  `find_form_xml` + a light XML header parse); `form: null` otherwise. This is the handle
  SP4b's reply-with-form consumes; the full variables are re-parsed from the stored XML
  when a reply is actually composed.

Retention rides SP3's rule: attachments cascade-delete with the `RawMessage`, which
survives until both the event and net session close — a form's attachment can't vanish
under a pending reply.

## Error handling

- Malformed `.b2f` (bad/oversize/non-numeric `File:` length, missing filename, truncation)
  → `B2FParseError`; `read_message_file` catches per-file, logs at debug, falls back to
  today's text-body-only extraction. One bad file never stalls a scan.
- Oversized attachments skipped with a warning (per-attachment + per-message caps).
- Filenames are metadata only — no path-traversal surface; download serves bytes with a
  sanitized filename and octet-stream type.
- `WinlinkBackend.send` refactor is behavior-preserving (byte-identical), guarded by the
  existing delivery tests + a new round-trip test.
- Attachment persistence failure is non-fatal to import.

## Testing

Pytest, no live mailbox:

- **Codec:** round-trip (`build_b2f`→`parse_b2f` equal), header quirks, multi-attachment,
  real-fixture parse (a form-bearing `.b2f`), malformed inputs → `B2FParseError` (never a
  crash).
- **`WinlinkBackend.send` byte-identity** — existing delivery tests unchanged.
- **Mailbox reader:** attachments extracted from `.b2f` and `.mime`/`.eml`; body extraction
  unregressed; malformed attachment → body-only fallback.
- **Import:** attachments persisted on new import; backfilled on re-scan; oversized skipped.
- **Received-form capture:** `find_form_xml` from attachment (new) and body (fallback);
  `parse_winlink_form_message` fed the right XML; `form` metadata block correct.
- **API:** attachments summary; download route (bytes, sanitized headers, cross-net 404,
  permission matrix); build gate for the frontend indicator.
- **Frontend:** 📎 indicator + download on the Messages panel; build gate.

## Non-goals (SP4a)

- Form composition / authoring, catalog, sandboxed fill, message builder,
  `RMS_Express_Form` XML *generation* (all SP4b).
- Reply-with-form UX (SP4b — SP4a only exposes the `reply_template` + variables it needs).
- Actually *sending* an attachment (the codec + backend support it; SP4b is the first
  consumer).
- The compressed B2 binary wire protocol (already-decoded `.b2f` files only).
- The newer JSON form format (future).
- General MIME features beyond attachment capture (inline images, deep nested multipart).
