# UX / Bug Backlog — 2026-06-20

**Source:** Items the user flagged during execution of the Winlink Forms
arc (commits `cdc46ba..a2ba576`). Captured here so a future session can
pick them up cold without conversational context.

Each item is a candidate for its own brainstorm → spec → plan cycle, or
can be tackled inline if the fix is small and the path is obvious.

---

## 1. Templates: `date` variable doesn't fit week-long nets

**Where:** Roster template rendering (`backend/modules/roster/service.py`,
look at the Jinja2 `build_roster_context` / `render_roster` path).

**Problem:** Templates use `{{ date }}` as a single scalar. For
multi-day sessions (e.g. "the week of June 14" when `start_date =
2026-06-14` and `end_date = 2026-06-20`), this renders as just the
start date, which is misleading.

**Proposed fix:** Two-part change.
- Change `{{ date }}` to render a smart string: single date for
  one-day nets, a range like "June 14 – 20" or "the week of June 14"
  for multi-day. Keep backward-compatible.
- Expose `{{ start_date }}` and `{{ end_date }}` as explicit variables
  for templates that want fine-grained control.

**Open questions:**
- Which "smart" format does the user prefer for multi-day?
  ("the week of X" vs "June 14 – 20" vs both, with selector)
- Update the shipped default templates at the same time?

---

## 2. Modals close on backdrop click anywhere in the app

**Where:** Generic — affects every modal in `frontend/src/`. Originally
reported on the template editor, where the user lost work 3 times.

**Problem:** Clicking the modal's backdrop closes it without warning.
Unsaved edits are silently discarded.

**Proposed fix:** Update the shared `Modal` component
(probably `frontend/src/components/Modal.tsx` — confirm path) so the
default behaviour is **no close on backdrop click anywhere**. Modals
that want it can opt in explicitly via a prop. The user's framing was
"clicking off a modal should not close in any place in the app, ever",
so opt-in (rather than opt-out) is the safer default.

**Open questions:** None — user was explicit.

---

## 3. "Default to current net" advances too early

**Where:** Probably `backend/modules/schedule/service.py` and/or
the frontend's session-selection helper. Look for whatever calls
`get_current_session` / `default_session` and how it picks the
"current" net.

**Problem:** Currently advances to the next scheduled net once the
previous net's scheduled window ends. But rosters are sent AFTER the
net, so the user always has to manually click back to the previous
net to finish the roster.

**Proposed fix:** A session should remain "current" until its roster
transitions to `RosterStatus.SENT` (or `SKIPPED` — operator explicitly
chose not to publish). Only then does the next scheduled session
become "current".

**Open questions:**
- What's the behaviour when no roster has been generated yet for the
  past session? (Stay on past session indefinitely? Some timeout?)
- What if there's no upcoming session scheduled?

---

## 4. Callbook lookup needs admin configuration UI

**Where:** `backend/integrations/callbook/` (service + cache table
exist; see `backend/integrations/callbook/models.py`). Frontend config
page: `frontend/src/pages/ConfigPage.tsx`.

**Problem:** The callbook integration is implemented but there's no
admin UI to configure it (API key / provider / etc.). User said
"callbook lookup is totally fucked because there is no configuration
for it" — implying it's failing silently or with a confusing error.

**Proposed fix:**
- Find what AppConfig keys the callbook integration expects (grep for
  `callbook.` in `get_config_value` calls).
- Add a section to the admin config page that surfaces and edits
  those keys (modeled after the existing SMTP / OAuth sections).
- Surface a useful error to the user when a lookup fails because
  config is missing (vs. some opaque 500).

**Open questions:**
- Which callbook provider(s) does the integration support? Check the
  service to know what fields the UI needs.

---

## 5. Parser hint: use `From:` header callsign more aggressively

**Where:** `backend/modules/checkins/service.py:92-94` already pulls
the callsign from `raw.from_address` when the parser returns nothing —
but only as a last-resort degraded path.

**Problem:** User said "When looking for the 'From' callsign in a
check-in, it's the sender. EZPZ". The sender address IS authoritative
for the sending callsign (it's the Winlink account that submitted the
message). Currently we trust the body's callsign first; if the body
disagrees with the sender, the body wins.

**Proposed fix:** Make the sender's callsign (from `From:` header) the
primary source of truth for `callsign`. If the body has a different
callsign, that becomes either a comment or a warning (manual_review),
not the parsed value. The body's callsign field is still useful for
distinguishing "Ben sent on behalf of Alice" from "Ben is checking
in himself" — but in practice the sender is who's on the air.

**Open questions:**
- Does the user want the body's callsign to be IGNORED, or used as a
  fallback when From: parsing fails?
- Should this affect existing check-ins that were parsed under the
  old assumption? (Probably no — only new parses going forward.)

---

## 6. Templates render duplicate comments

**Where:** `backend/modules/roster/service.py` —
`build_roster_context` and the default templates (`seeds.py`).

**Problem:** Check-in objects already include `comments`. The shipped
templates render check-ins, then ALSO render a separate "Comments"
section that re-prints the same comments. Visible duplication in the
output.

**Proposed fix:** Remove the separate "Comments" section from the
default templates AND from `build_roster_context` (so the variable
that fed it goes away). Migrate existing user-customized templates by
either (a) leaving them as-is (operator's choice) or (b) shipping a
migration that strips the duplicate section.

**Open questions:**
- Do operator-customized templates need any migration?
- Or just update the shipped defaults + let operators manually re-clone?

---

## 7. Groups.io delivery: "no backends are configured"

**Where:** `backend/integrations/delivery/` — `service.py` and
`dispatch_delivery`.

**Problem:** User tried posting to groups.io and got "no backends are
configured" — but groups.io is... (user message was truncated).

**Status:** Cannot triage without the full message. Either:
- groups.io is one of the supported backends but isn't enabled in this
  install (configuration gap, similar to item 4 — needs admin UI)
- groups.io ISN'T a supported backend and the user wants it added
- groups.io support exists but the dispatch path doesn't recognize the
  destination format

**Next step:** Ask the user to complete the sentence "groups.io
is...?" before designing.

---

## 8. Notifications render off the left side of the browser window

**Where:** `frontend/src/components/Notification*.tsx` (or wherever
the toast/notification component lives) + its CSS positioning.

**Problem:** The notifications toast container is positioned such that
it renders off-screen to the left (probably `left: -<something>` or a
negative transform that overflows the viewport).

**Proposed fix:** Inspect the positioning CSS; switch to a viewport-
anchored position (e.g., `right: 1rem; top: 1rem`) so toasts always
stay visible. Verify on narrow viewports too.

**Open questions:** None — purely a CSS positioning fix once the
component is found.

---

## Pickup order suggestion

Quick wins first (low effort, high visibility):
1. Item 2 — modal backdrop fix (single component, opt-in flag, ships
   across the whole app)
2. Item 8 — notifications off-screen (CSS fix)
3. Item 6 — duplicate comments in templates (template + context cleanup)

Medium effort:
4. Item 1 — week-long date variable
5. Item 3 — current-net advance condition
6. Item 5 — sender-callsign primacy

Larger / needs design input:
7. Item 4 — callbook config UI (need to inventory what the integration
   expects first)
8. Item 7 — groups.io (need clarification on what's broken)
