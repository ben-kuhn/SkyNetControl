# Event Winlink Messaging Design

**Date:** 2026-07-16
**Status:** Approved for planning

## Context

Sub-project 3 of live events (see `2026-07-15-live-events-design.md`, shipped, and
`2026-07-15-live-map-aprs-design.md`, shipped). Events now have participants with a
status lifecycle, posts, a cursor-polled dashboard with a unified log, and a live APRS
map. This sub-project adds **Winlink message traffic scoped to an active event**:
inbound net-addressed messages routed into a per-event Messages panel, and outbound
plain-text send/reply under the net callsign.

During brainstorming the original "Winlink traffic" idea decomposed into four
sub-projects; this spec is the first:

- **SP3 — Event Winlink messaging core** (this spec): inbound dual-ingest routing,
  Messages panel, participant-linking, log breadcrumbs, outbound plain-text send/reply,
  manual re-scan.
- **SP4 — Winlink forms composition:** PAT-style arbitrary-form authoring (the form's
  own JS runs sandboxed in the operator's browser and posts computed variables back;
  the server does no-JS template→message composition). Builds on SP3's outbound path.
  Attachment handling (forms travel as attachments) lands here.
- **SP5 — PAT transport control:** parse PAT config for connection aliases; let NCO
  trigger `pat connect` via a structured mode/gateway/frequency selection (no free-form
  input — command-injection risk); app-wide (also serves net check-ins).
- **SP6 — Weather overlay.**

Prior decisions from user Q&A:

- Inbound association: **all net-addressed traffic while the event is active** routes to
  the event. Caveat: an event may overlap a regular net; in that case a message is
  ingested into **both** the event and the net check-in session, and NCO triages each
  independently. The shared `RawMessage` is retained until **both** the event and the
  net session are closed.
- Inbound triage lives in a **dedicated Messages panel**, participant-linked by
  from-callsign match, with a one-line breadcrumb dropped into the unified event log.
- Inbound arrives via the existing **background scanner** plus a manual **"Re-scan
  mailbox"** button (no special fast auto-interval).
- Outbound goes out **under the net callsign** (operator attribution stays internal).
- Message state is a **single shared status** (unread → read → dismissed), not
  per-operator. Threading is **explicit only** (our replies link to their parent; no
  inbound-to-inbound auto-threading).
- Attachments are **deferred to SP4** (text bodies only here).
- `to_address` validation is **permissive**: accepts callsign / Winlink / standard
  internet email (Winlink CMS relays to email; third-party recipients are legitimate).

## Requirements

- While an event is `active`, every net-matched inbound Winlink message produces an
  `EventMessage` for that event, in addition to any check-in it produces for an active
  net session (dual-ingest from a shared, deduped `RawMessage`).
- Inbound messages link to a checked-in participant when the from-callsign matches
  (SSID-stripped), and drop a `system` breadcrumb into the event log.
- A dedicated Messages panel on the event dashboard lists inbound + outbound messages
  with shared status (unread/read/dismissed), unread count, and client-side filters.
- NCS can compose a new plain-text message and reply to an inbound one; sends go out
  under the net callsign via the existing Winlink delivery backend and are retryable on
  failure.
- A manual "Re-scan mailbox" action reads the mailbox now and routes new traffic.
- Read-only members see the panel and bodies but no write controls.
- Winlink/event failures never affect event operation.

## Data model

New `EventMessage` model in `backend/modules/events/models.py`. One table.

### `event_messages`

| Column | Notes |
|---|---|
| `id` | PK |
| `event_id` | FK → events.id |
| `msg_seq` | monotonic per event — the Messages-panel polling cursor (same pattern as `log_seq`) |
| `direction` | enum: `inbound` \| `outbound` |
| `raw_message_id` | FK → raw_messages, nullable (set for inbound; null for outbound) |
| `participant_id` | FK → event_participants, nullable (set when from-callsign matches) |
| `from_callsign` | sender (inbound) or net callsign (outbound) |
| `to_address` | recipient (outbound); the net address the message came to (inbound) |
| `subject` | |
| `body` | Text |
| `status` | enum: `unread` \| `read` \| `dismissed` (single shared status) |
| `reply_to_id` | self-FK → event_messages, nullable (outbound reply → the inbound it answers) |
| `actor` | callsign of the sending operator (outbound); null for inbound |
| `received_at` | inbound: message time; outbound: send time |
| `created_at` | |

**Cursor:** `msg_seq` is assigned per-event via the same locked-counter mechanism as
`Event.log_seq` (a new `Event.msg_seq` counter column, incremented under the event row
lock). The Messages panel polls `?since=msg_seq`.

**Dedup:** unique constraint on `(event_id, raw_message_id)` (raw_message_id NOT NULL).
Re-running the scan — background or manual — cannot create a second inbound
`EventMessage` for the same raw message in the same event; a per-message
IntegrityError is caught and skipped (mirrors the check-in path). Outbound rows have
null `raw_message_id` and are unconstrained.

**Retention:** `RawMessage` rows are shared across the check-in and event flows.
Dismissing an `EventMessage` flips its `status` only — it never deletes the
`RawMessage` or the sibling `CheckIn`. The app has no aggressive raw-message GC today;
this spec's guarantee is explicitly *don't cascade-delete a shared raw message* — a raw
message is eligible for cleanup only when it has no live reference from either an active
event (a non-dismissed `EventMessage`) or an active net session's check-in.

**Outbound delivery status** is NOT duplicated onto `EventMessage`; it rides the
existing `DeliveryLog` with `content_type="event_message"`, `content_id` = the
EventMessage id.

## Inbound routing

New `route_event_messages(db, net_id, raw_messages)` in
`backend/modules/events/messages.py`, called from the scanner cycle immediately after
the existing `scan_and_import_messages()` check-in pass — both consume the same list of
net-matched, deduped raw messages.

For each `active` event of the net, for each raw message:

- Skip if an `EventMessage` already exists for `(event_id, raw_message_id)`.
- Extract the sender's base callsign (SSID-stripped, uppercased) from `from_address`;
  if it matches a checked-in `EventParticipant`, set `participant_id`.
- Create the `EventMessage` (direction=inbound, status=unread, subject/body/from copied
  from the raw message, `received_at` from the message), assigning the next `msg_seq`.
- Write a `system` breadcrumb to the event log via `add_log_entry`:
  `"📩 Winlink from <CALLSIGN>: <subject>"`, tagged with the callsign so the
  participant's detail panel surfaces it.

**Two triggers, one code path:**

1. **Background scanner** — the existing loop at the net's `scanner.interval_minutes`.
   The event pass runs whenever the scanner runs, gated only on the event being active
   (no session window required — the key divergence from check-ins).
2. **Manual re-scan** — `POST /api/nets/{slug}/events/{id}/rescan` (NCS): reads the
   mailbox now and runs the standard routing (which covers every active event of the net
   plus the check-in session, exactly as the background scan does — re-reading the shared
   mailbox is idempotent thanks to the dedup constraint). The response reports the count
   of new messages for **the calling event** specifically.

**Dual-ingest:** when a check-in session and an event are both active, one inbound
message yields both a `CheckIn` and an `EventMessage` from the shared `RawMessage`;
neither dismiss affects the other.

**Enablement:** event ingestion requires the net's `scanner.enabled` +
`pat_mailbox_path` + `net_address` (same config the check-in scanner uses). With no
mailbox configured, events have no inbound and the panel shows a hint.

## Outbound send & reply

Reuse the existing delivery pipeline — no new backend, no new send code.

- Extend `dispatch_delivery()`'s `_lookup_content()` for `content_type="event_message"`:
  read `subject`/`body` from the `EventMessage`, resolve `net_id` via the event (cross-
  net isolation, mirroring reminder/roster resolving via session). Send goes through
  `WinlinkBackend` as roster/reminder do — header-injection guards, `.b2f` to
  `{mailbox_path}/out`, under the net's `net_address`/callsign, `DeliveryLog` recorded.
- `POST /api/nets/{slug}/events/{id}/messages` (NCS) — body
  `{to_address, subject, body, reply_to_id?}`. Creates an outbound `EventMessage`
  (direction=outbound, status=read, actor=operator callsign, to_address, reply_to_id
  when replying), then calls `dispatch_delivery(..., "event_message", id, ...)`.
  Returns the message + delivery result.
- **Reply** is the same endpoint with `reply_to_id` set. Frontend prefills `to_address`
  from the inbound `from_callsign`, `subject` as `Re: <original>` (collapsing an existing
  `Re:`), and quotes the original body (`> ` prefix) — all editable.
- **Delivery failure** persists the outbound row with `DeliveryLog` failed; the existing
  `GET /delivery/event_message/{id}` and `POST .../retry` work once the content type is
  registered — retry needs no new plumbing. Drafted content is never lost.

**Guardrails:**

- Send/reply/dismiss/rescan require `net_control` (or admin); viewers read only.
- Allowed only while the event is `active` — writes against a closed event → 409
  (matching SP1).
- `to_address` validation is **permissive on format**: accept Winlink callsign
  addresses (`KE0XYZ`, `KE0XYZ@winlink.org`), tactical addresses, and standard internet
  email (`name@domain`). Reject only empty / oversized / control-char input; always
  strip CR/LF (the backend's `_strip_b2f_header_chars` is the header-injection backstop).
  Winlink's CMS does the routing — sanitize, do not gatekeep.

## Frontend

New components under `frontend/src/pages/events/`; API additions mirror SP1/SP2
(`apiFetch`, cursor polling). No new libraries.

**`MessagesPanel`** — a tab/section on the event dashboard beside the participant board
and net log:

- Message list newest-first: from-callsign (linked to participant when matched),
  subject, received time, unread dot. Unread-count badge on the tab.
- Client-side filter chips: All / Unread / Inbound / Outbound (no pagination).
- Row opens the message: full body; for a thread, the linked reply(ies) beneath. Opening
  an unread inbound message flips it to `read` (PATCH).
- Per-message NCS actions: **Reply** (composer prefilled), **Dismiss** (→ dismissed,
  hidden from default view; an "include dismissed" toggle restores them).

**Composer** — modal for new message or reply: `to_address`, `subject`, `body`, with the
reply-prefill from the outbound section. Submit calls the compose endpoint; delivery
result via toast; a failed send shows a retry affordance wired to the existing delivery
retry.

**Re-scan** — a "Check mail now" button on the panel header (NCS, active event) calling
`POST .../rescan`, toasting the count of new messages.

**Live updates** — a dedicated `useEventMessages(netSlug, eventId, enabled)` hook polling
`/messages?since=msg_seq`, so message bodies only flow to clients viewing the panel
(same discipline as the positions hook). Cursor-accumulate like the log.

**Read-only viewers** see the panel and bodies; no compose/reply/dismiss/rescan controls.

## Error handling

- Mailbox unreadable / not configured → event ingestion no-ops; panel shows a
  "no mailbox configured" hint; the event is unaffected.
- Malformed inbound file → `read_mailbox()` already skips it; routing sees only
  well-formed messages.
- Send failure → outbound row persists with `DeliveryLog` failed; NCO retries via the
  existing delivery path; drafted content is never lost.
- Re-scan overlapping a background scan → the `(event_id, raw_message_id)` unique
  constraint makes double-ingest a no-op (per-message IntegrityError caught).
- Write against a closed event → 409.

## Testing

Pytest, no live mailbox:

- Routing: dual-ingest (one raw message → check-in + event message), participant-linking
  by SSID-stripped callsign, dedup on re-scan, per-active-event fan-out, breadcrumb
  written to the event log.
- Retention: dismissing an `EventMessage` leaves `RawMessage` and the sibling `CheckIn`
  intact.
- Outbound: `event_message` content-type lookup + net_id resolution, `dispatch_delivery`
  through a fake backend, reply threading (`reply_to_id`), `to_address` validation
  (accepts callsign / Winlink / internet email; rejects CR/LF / empty / oversized).
- Routes: permission matrix (viewer / net_control / admin / non-member), closed-event
  409, `?since=` cursor semantics, cross-net 404 scoping.
- Frontend: build gate + manual smoke.

## Non-goals (this sub-project)

- Attachments of any kind (SP4 — forms travel as attachments; extraction lands there).
- Winlink forms composition (SP4).
- PAT transport/connection control — aliases, `pat connect`, gateway/mode/frequency (SP5).
- Weather overlay (SP6).
- Auto inbound-to-inbound threading; per-operator unread; outbound under individual
  operator callsigns.
