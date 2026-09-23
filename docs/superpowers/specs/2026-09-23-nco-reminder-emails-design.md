# NCO Reminder Email Design

Date: 2026-09-23
Status: Draft

## Problem

Reminder drafts auto-generate (on page load and via `generate_due_drafts`), but
nothing tells net control (NCO) *to go send one*. The reminder the operator is
supposed to dispatch the day before a net can sit in the draft list unnoticed.

## Goals

- The day before a scheduled net/event, email the NCO at a morning time
  (default **07:00 local**) with a direct link to the auto-generated reminder
  draft for review and one-click send.
- If the reminder still has not been **sent** by an evening time
  (default **19:00 local**) the same day, send a follow-up email.
- Only applies to scheduled nets/events — **never week-long sessions**.
- No duplicate emails per session/threshold (persisted dedup).
- Disabled by default; fully configurable per net.

## Non-goals

- No day-of or post-hoc catch-up emailing (only the calendar day before the
  net; a missed day is simply missed).
- No change to the reminder state machine or delivery backends.
- No in-app UI beyond per-net config fields.

## Key decisions

1. **Timezone** — the app has no timezone concept today (all timestamps UTC,
   `NetSeason.time` is display-only). This feature therefore adds a per-net
   `reminders.nco_email_timezone` (IANA zone) key. When unset it falls back to
   the server's local zone (`ZoneInfo("localtime")`), then UTC. Operators are
   expected to set it explicitly.
2. **Recipient** — resolve the human NCO for the session
   (`net_session.net_control_callsign`, falling back to the net's default net
   control, then the lowest-id admin), look up that callsign's `User.email`, and
   fall back to an explicit per-net `reminders.nco_email_to` override. Skip the
   email (with a log line) if no address can be resolved.
3. **Draft link** — the loop runs `generate_due_drafts(net_id)` first so a draft
   exists, then links to
   `<app_base_url>/nets/<net_slug>/reminders?focus=<reminder_id>`. If no draft
   exists (e.g. no default template), link generically to the reminders page.
4. **"Not done"** — an email is suppressed when the session's reminder is
   already `SENT` *or deliberately `SKIPPED`* (discard is treated as a decision,
   not an oversight). Nagging happens only for missing/DRAFT/APPROVED reminders.
5. **Background loop** — mirrors the existing `scanner_loop` pattern
   (`backend/app.py` lifespan + a config-driven interval), so it works on the
   NixOS deployment without any systemd/timer changes.

## Config keys (per-net `net_config`)

| Key | Type | Default |
|-----|------|---------|
| `reminders.nco_email_enabled` | bool | `false` |
| `reminders.nco_email_timezone` | IANA zone string | server local (`ZoneInfo("localtime")`), else UTC |
| `reminders.nco_email_morning_time` | `HH:MM` | `07:00` |
| `reminders.nco_email_evening_time` | `HH:MM` | `19:00` |
| `reminders.nco_email_to` | email (optional override) | empty |
| `reminders.nco_email_check_interval_minutes` | int | `5` |

Documented in `docs/deployment/app-config-keys.md`.

## Data model

New table `nco_reminder_logs` (dedup + audit trail), Alembic migration:

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer | PK autoincrement |
| `session_id` | Integer | FK `net_sessions.id` |
| `kind` | Enum | `morning` / `evening` |
| `emailed_at` | DateTime(tz) | |

Unique constraint `(session_id, kind)` guarantees at most one email per
threshold per session.

## Backend

New module `backend/modules/reminders/nco.py`:

- `run_nco_reminder_checks(db) -> None` — the per-tick workhorse:
  1. For each net with `reminders.nco_email_enabled == "true"`:
     a. Call `generate_due_drafts(db, net_id=net_id)` so drafts exist.
     b. Query `SCHEDULED` sessions of that net where
        `(session.start_date - today).days == 1` (tomorrow) and the session is
        not week-long.
     c. Resolve the reminder for each session; skip if it is `SENT` or `SKIPPED`.
     d. Compute local `now` in the net's configured zone.
     e. If `morning_time <= now < evening_time` and no `morning` log → send
        morning email + insert log.
     f. If `now >= evening_time` and no `evening` log → send evening email +
        insert log.
- `nco_reminder_loop(session_factory, get_interval) -> None` — the async loop,
  structured like `scanner_loop`.

`backend/app.py` lifespan starts the loop (best-effort, try/except, like the
scanner).

Email is sent via the existing fire-and-forget `backend/auth/email.py::send_email`
(no new DeliveryLog rows; those are for content distribution, not operator
notices). SMTP must be configured (`smtp.*` keys) or emails are silently
skipped — same behavior as existing admin notification emails.

Week-long detection: `session.season.is_week_long`, or for season-less sessions,
`(end_date - start_date).days > 1` (weekly nets have a 1-day window).

## Frontend

`frontend/src/pages/NetSettingsPage.tsx`: add a "NCO reminder emails" field group
with the enabled toggle, timezone, morning/evening times, and optional recipient
address, wired through the existing `setNetConfigBulk` path.

## Tests

- Morning email fires once for a tomorrow-session inside the morning window;
  dedup prevents a second send on the next tick.
- Evening email fires when the reminder is still unsent; suppressed when SENT or
  SKIPPED.
- Week-long sessions are never emailed.
- Non-tomorrow sessions (past, today, >1 day out) are never emailed.
- Recipient resolution: session NCO's `User.email`; config override; skip when
  unresolvable.
- Time window edge cases: before morning → no email; between morning/evening →
  morning only; after evening → evening only.

## Affected files

| File | Change |
|------|--------|
| `backend/modules/reminders/models.py` | add `NcoReminderKind`, `NcoReminderLog` |
| `alembic/versions/<new>.py` | create `nco_reminder_logs` |
| `backend/modules/reminders/nco.py` | new: checks + loop |
| `backend/app.py` | start loop in lifespan |
| `frontend/src/pages/NetSettingsPage.tsx` | config field group |
| `docs/deployment/app-config-keys.md` | document new keys |
| `tests/test_reminder_nco.py` | coverage for all of the above |