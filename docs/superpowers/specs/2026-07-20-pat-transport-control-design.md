# PAT Transport Control Design (SP5)

**Date:** 2026-07-20
**Status:** Approved for planning

## Context

SkyNetControl composes and receives Winlink traffic (rosters, reminders, event
messages, SP4b forms) but never actually drives the radio. Today's model is a
pure **file-based handoff**:

- **Outbound:** `WinlinkBackend.send` builds a `.b2f` and writes it to
  `{pat_mailbox_path}/out/`; PAT's own daemon transmits it on its own schedule.
  The delivery row is marked `SENT` the instant the file is written (optimistic).
- **Inbound:** the scanner polls `{pat_mailbox_path}/in/` on a timer and imports
  whatever PAT has already received.

PAT (the Winlink client) is never invoked by the app, so the operator cannot
force a send/receive during a live event, and the app has no visibility into
whether traffic actually moved over the air.

SP5 replaces the file handoff with **PAT's HTTP API** and adds **operator-driven
connect control** with live session progress. This is also the prerequisite that
unblocks the SP4a/SP4b **interop pinning gate** (a real `.b2f` round-trip through
real Winlink clients) — once the app can drive a connection, a real round-trip
becomes possible. The pinning validation itself remains a manual step after SP5.

### Binding decisions (from the SP5 Q&A)

1. **Topology:** PAT runs on a **remote host reached over HTTP** (PAT's built-in
   HTTP API). Same-box and other-LAN-box are both in scope; the app is not
   assumed to share PAT's filesystem.
2. **Message transfer:** **full HTTP via PAT's API** — outbound posted and
   inbound fetched over HTTP; no shared mailbox filesystem required. This
   replaces the current file-based delivery and scanner paths.
3. **Auth:** support **both** a trusted private network (base URL only, no
   credentials) **and** a fronting reverse proxy (**Basic auth or bearer token**,
   stored in the existing secret box). Optional credential; base URL always.
4. **Connect method:** **PAT connect aliases** (read from PAT's API) as the
   primary picker, plus a **structured advanced builder** (mode + gateway +
   optional frequency) for ad-hoc connects.
5. **Live progress:** **live session progress** — the backend consumes PAT's
   status stream and records session events; the UI shows a running session log
   + counts via the app's existing cursor-polling (no new browser-websocket
   machinery).
6. **Connect lifecycle:** a **background task + polling** engine — the connect
   endpoint starts an async background task and returns a session id immediately;
   the UI polls session status.
7. **Scope:** built as a **single SP5 spec** (not split into SP5a/SP5b), with
   clean internal component boundaries.
8. **Status semantics:** introduce `QUEUED` (posted to PAT) → `SENT` (confirmed
   left PAT's outbox after a connect). This corrects today's optimistic status
   for **all** winlink delivery (rosters/reminders/events), not just forms.

## PAT HTTP API surface (verified against `la5nta/pat` `api/api.go`, post-v1.0.0)

- `GET /api/connect?url=<connectStr>` — trigger a connection; blocks for the
  session, returns success/failure. `GET /api/disconnect` aborts.
- `POST /api/mailbox/{box}` — post an outbound message (multipart: `to`,
  `subject`, `body`, `cc`, and file attachments). PAT assembles the on-air
  message. Box = `out`.
- `GET /api/mailbox/{box}` — list a mailbox (`in`/`out`/`sent`/`archive`), JSON.
- `GET /api/mailbox/{box}/{mid}` — single message JSON (body + attachment list).
- `GET /api/mailbox/{box}/{mid}/{attachment}` — attachment bytes.
- `POST /api/mailbox/{box}/{mid}/read` — mark read. `DELETE …/{mid}` — delete.
- `GET /api/status` — connection/active status.
- `GET /api/config/connect_aliases` — the saved alias→URL map.
- `GET /api/rmslist` — RMS gateway list (modes/frequencies) for the structured
  builder / gateway typeahead.
- `POST /api/qsy` — set frequency (rig control); frequency is normally passed in
  the connect URL `?freq=` instead.
- `/ws` — websocket streaming live status/notifications/progress (the live
  session feed the backend consumes).

**Implication:** because PAT assembles the outbound message from posted
components, the SP4a `.b2f` **outbound** codec largely leaves the transmit path
(PAT builds it). The SP4b **form XML** correctness question remains — the
`RMS_Express_Form_*.xml` is posted as a multipart attachment. The inbound `.b2f`
codec is still used to parse messages fetched over HTTP.

## Architecture

### Component: `backend/integrations/winlink/pat_client.py`

A thin HTTP client wrapping the PAT endpoints — the single seam every consumer
and every test goes through.

- Base URL + optional auth (`none` | `basic` | `token`) from config; auth header
  injected per request.
- Per-call timeouts; a longer session timeout for `connect`.
- Typed errors: `PatUnavailable` (transport/connection/auth failure, PAT down),
  `PatConnectError` (a connect attempt that PAT rejected/failed).
- Methods (indicative): `post_outbound(to, subject, body, cc, attachments)`,
  `list_mailbox(box)`, `get_message(box, mid)`, `get_attachment(box, mid, name)`,
  `connect(connect_url)`, `disconnect()`, `status()`, `connect_aliases()`,
  `rmslist()`, and a `stream_status()` consumer over `/ws`.
- No business logic; pure transport. Tested against a fake PAT server /
  monkeypatched httpx — **no live radio in CI**.

### Consumer 1: Outbound migration (`WinlinkBackend.send`)

- Instead of building a `.b2f` and writing `out/`, `POST`s recipient + subject +
  body + attachments (the SP4b form XML included as a multipart file) to
  `/api/mailbox/out`.
- On success the delivery row is `QUEUED` (in PAT's outbox), **not** `SENT`.
- On failure (`PatUnavailable`, HTTP error) the delivery attempt is marked
  failed/retryable — never a silent success. The message is not lost; a retry
  re-posts.
- When `pat_transport_enabled=false`, falls back to today's file-based write
  (safety valve / migration switch).

### Consumer 2: Inbound migration (scanner)

- The scanner stops reading `{mailbox}/in/` and instead lists `/api/mailbox/in`,
  fetching each not-yet-imported message + its attachments over HTTP.
- The bytes then feed the **existing** parse/import path unchanged: B2F/MIME
  parsing, SP4a form capture (`find_form_xml` + variables), attachment
  persistence, dedupe by message id. Only the *source* of the bytes changes.
- Runs on the existing scanner cadence, and is also invoked immediately at the
  end of a connect session (so received traffic appears promptly).

### Consumer 3: Connect/session engine (`backend/integrations/winlink/pat_session.py`)

- The connect endpoint resolves the chosen alias or structured input into a
  connect URL, creates a `pat_connection_sessions` row (`connecting`), starts a
  background asyncio task, and returns `{session_id}` immediately.
- The task: fires `GET /api/connect?url=…` (blocking — run in a thread via
  `asyncio.to_thread`) while **concurrently** consuming `/ws`, appending status
  lines to the session's event log and advancing the phase
  (`connecting → connected → syncing`).
- On connect return: run the inbound fetch (import received traffic), then
  **reconcile outbound** — messages that left PAT's `out` box flip
  `QUEUED → SENT` and record `pat_session_id`; tallies update
  `sent_count`/`received_count`; status → `completed` (or `failed`).
- **Single-flight lock:** one session at a time. A second trigger returns 409
  ("session already running"). PAT's own scheduled auto-connects are outside our
  lock; a collision surfaces as a clean session failure with PAT's busy error.
- Managed in the FastAPI lifespan like the scanner loop / APRS manager
  (background task lifecycle, cancellation on shutdown).

### Connect-method resolution

- **Alias (primary):** `GET /pat/connect-options` returns PAT's
  `connect_aliases` (name→URL map); the operator picks one; the engine resolves
  the alias to its URL from that map and sends the URL to `/api/connect`.
- **Structured advanced:** mode (`telnet`/`ardop`/`vara`/`packet`/`pactor`) +
  gateway callsign (typeahead from `/api/rmslist`) + optional frequency →
  composed into a PAT connect URL (e.g. `ardop:///GATEWAY?freq=7100`). A
  `method_label` is stored for display.

## Data model

New table `pat_connection_sessions`:

| Column | Notes |
|---|---|
| `id` | PK |
| `net_id` | FK → nets (attribution; connect is station-global) |
| `event_id` | nullable FK → events (set when triggered from an event panel) |
| `connect_url` | resolved connect string sent to PAT (alias-expanded) |
| `method_label` | display label ("alias: ardop-gw1" / "ardop KE0XYZ @ 7100") |
| `status` | `connecting`/`connected`/`syncing`/`completed`/`failed`/`aborted` |
| `sent_count` | reconciled outbound tally |
| `received_count` | imported inbound tally |
| `error` | failure detail (nullable) |
| `events` | JSON array of `{ts, kind, text}` session-log lines from `/ws` |
| `actor` | callsign who triggered |
| `started_at` / `ended_at` | timestamps |

Session-log lines live inline as bounded JSON (small per session; same pattern as
other event JSON columns) — no separate events table.

Delivery reconciliation reuses `DeliveryLog`:
- Add `QUEUED` to `DeliveryStatus`.
- Add nullable `pat_session_id` FK so a message shows which session carried it.
- Add nullable `pat_mid` (the message id PAT returns when the outbound is posted).
  Reconciliation is precise: after a connect, a `QUEUED` row whose `pat_mid` is no
  longer present in PAT's `out` box (moved to `sent`) flips to `SENT`. If PAT does
  not return a usable id on post, reconciliation falls back to a best-effort
  outbox diff and, when ambiguous, leaves the row `QUEUED`.

## Config & secrets

Global keys with per-net override (mirrors the existing `pat_mailbox_path`
pattern):

- `pat_http_base_url` — e.g. `http://shack:8080` (enables HTTP transport)
- `pat_http_auth_mode` — `none` | `basic` | `token`
- `pat_http_username` / `pat_http_password` / `pat_http_token` — **stored in the
  secret box**; write-only in the admin UI, never returned in plaintext by config
  reads (like other secrets)
- `pat_http_timeout_seconds` — default for ordinary calls (connect uses a longer
  session timeout)
- `pat_transport_enabled` — bool; `false` falls back to today's file-based
  handoff

## API surface

All `NET_CONTROL`-gated:

- `POST /api/nets/{slug}/events/{id}/pat/connect` — body `{alias}` or
  `{mode, gateway, freq?}`; starts a session, returns `{session_id}`; **409** if a
  session is already running.
- `GET /api/nets/{slug}/pat/sessions/{session_id}` — session status + event log +
  counts (the live-progress poll).
- `POST /api/nets/{slug}/pat/sessions/{session_id}/abort` — calls PAT
  `/api/disconnect`; session ends `aborted`.
- `GET /api/nets/{slug}/pat/connect-options` — aliases + RMS gateway list for the
  picker.
- `POST /api/nets/{slug}/pat/test` — connectivity check (hits `/api/status`);
  green/red like the existing groups.io test.

A net-scoped connect entry (no `event_id`) also exists for async-net use; the
event Messages panel is the primary entry.

## UI

Event Messages panel (`NET_CONTROL` + active event):

- **Connect** button in the panel header opens a modal. Primary control: an
  **alias dropdown** from `/pat/connect-options`. An **Advanced** disclosure
  reveals the structured builder — mode + gateway (typeahead from the RMS list) +
  optional frequency.
- On trigger the modal becomes a **live session panel**: a phase badge
  (`connecting → connected → syncing → done/failed`), a scrolling **session log**
  (PAT status lines as they arrive), running **sent/received counts**, and an
  **Abort** button. Fed by polling `GET /pat/sessions/{id}` on the app's existing
  cursor-poll cadence.
- Message rows show real status: `QUEUED` (📤 waiting for a connect) vs `SENT`
  (✓ carried by session #N). Inbound messages imported during a session appear in
  the panel.
- Net admin config gains the PAT-HTTP fields + a **Test connection** button.

## Error handling & concurrency

- **Single-flight:** one session at a time (409 on a second trigger). PAT's own
  scheduled auto-connect is outside the lock; a collision fails the session
  cleanly with PAT's busy error.
- **PAT unreachable** (bad base URL, PAT down, bad auth) → `PatUnavailable`;
  connect/test/send surface a clear operator error, never a 500. Outbound post
  failure keeps the message `QUEUED` and marks the attempt failed (retryable).
- **Session timeout/stall:** a session exceeding the configured session timeout
  is marked `failed` (timeout error) and PAT is sent `/api/disconnect`.
- **Abort:** `/api/disconnect`; session ends `aborted`.
- **Reconciliation is best-effort:** if outbox state can't be confirmed after a
  connect, messages stay `QUEUED` (safe) rather than falsely `SENT`.
- **`pat_transport_enabled=false`** falls back to the file handoff, so a broken
  PAT-HTTP config can't strand the station.

## Testing

Pytest, no live radio/PAT:

- **`pat_client`** — fake PAT HTTP server / monkeypatched httpx: post outbound
  (multipart + attachment), list/get inbound + attachments, connect
  success/failure, aliases/rmslist parse, auth header injection, error mapping.
- **Outbound migration** — `WinlinkBackend.send` posts the correct multipart incl.
  the form XML; `QUEUED` status; existing delivery tests updated (on-disk `.b2f`
  assertions replaced by posted-payload assertions).
- **Inbound migration** — scanner imports from HTTP-sourced messages; existing
  parse/form-capture/attachment tests reused with an HTTP source shim; dedupe
  unchanged.
- **Session engine** — a fake `/ws` event stream drives a full session: status
  transitions, event-log accumulation, sent/received reconciliation, single-flight
  409, abort, timeout, PAT-unreachable.
- **Frontend** — build-gated: connect modal (alias + advanced), live session panel
  polling, message status badges.

## Non-goals (this spec)

- Driving PAT's *scheduled* auto-connect (coexists; not managed by the app).
- Rig control / hamlib directly — frequency is passed in the connect URL; PAT +
  hamlib perform the QSY.
- PAT's own forms manager (SP4b's builder is retained).
- Multi-radio / concurrent sessions.
- The interop pinning validation itself — SP5 *enables* the real round-trip; the
  pinning remains a manual step afterward (see SP4a/SP4b gates).
- The newer Winlink JSON form format (future; noted in SP4).
