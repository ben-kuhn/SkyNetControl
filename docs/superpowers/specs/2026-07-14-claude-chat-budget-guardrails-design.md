# Claude Chat Budget Guardrails — Design

**Date:** 2026-07-14
**Status:** Approved

## Problem

The Activities page's brainstorm chat (`backend/modules/activities/chat_service.py`) calls the
Anthropic API with the operator's own key (`claude_api_key` in AppConfig). Today nothing limits
what it can be used for or how much it can be used:

- The system prompt steers Claude toward net-activity brainstorming but never instructs it to
  refuse off-topic requests, so any NET_CONTROL user can chat about anything on the operator's dime.
- There are no volume limits: no per-user or global message caps.
- Every message re-sends the entire conversation history, so long chats grow progressively more
  expensive with no ceiling.
- The model is pinned to `claude-sonnet-4-20250514`, which is deprecated (retires June 2026).

## Goals

1. Restrict the chat to amateur-radio net topics (all modes — Winlink, packet, other digital
   modes, CW, analog/phone, etc.), not just Winlink.
2. Bound worst-case API spend with hard, admin-tunable usage caps.
3. Bound per-request cost growth from long conversations.

Non-goals: a pre-classifier gate (users are trusted NET_CONTROL operators and the caps bound
worst-case spend), per-net caps, token-based accounting, UI redesign.

## Design

### 1. Topic guard (system prompt rewrite)

Replace `SYSTEM_PROMPT` in `chat_service.py` with a prompt that:

- Frames the assistant as helping run amateur-radio nets and design net activities for nets of
  any mode: Winlink, packet, other digital modes, CW, analog/phone, etc. General ham-radio
  questions in service of running a net (band conditions, message formats, training ideas,
  emergency-communications practice) are in scope.
- Firmly declines anything unrelated to amateur radio or net operations with a one-sentence
  redirect back on topic, and instructs Claude not to comply even if the user insists, rephrases,
  or claims special permission. Refusals are short, so off-topic attempts cost almost nothing.
- Keeps the existing activity-output guidance (clear title, brief description, detailed markdown
  instructions suitable for sending to participants).

### 2. Usage caps (hard enforcement in the backend)

**Config.** Two new AppConfig keys, editable in the admin config UI alongside the existing
`claude_api_key`:

| Key | Default | Meaning |
|---|---|---|
| `claude_daily_user_message_limit` | `25` | Max user messages per callsign per UTC day. `0` = unlimited. |
| `claude_daily_global_message_limit` | `100` | Max user messages across all users per UTC day. `0` = unlimited. |

**Attribution.** New nullable `sender_callsign` column on `chat_messages` (alembic migration).
The send route records the calling user's callsign on each USER-role message. Existing rows stay
NULL; NULL rows still count toward the global cap but not toward any per-user cap.

**Enforcement.** In the send-message route, *before* calling the Anthropic API:

1. Count today's (UTC) `ChatMessage` rows with `role = USER` for the caller's callsign; if the
   per-user limit is nonzero and reached, return **429**.
2. Count today's USER-role rows across all sessions; if the global limit is nonzero and reached,
   return **429**.

The 429 detail is a friendly message, e.g. "Daily chat limit reached — resets at midnight UTC."
No API spend occurs on a capped request. The frontend (`BrainstormPanel.tsx`) surfaces the 429
detail inline instead of a generic failure message.

### 3. Per-request cost bounds

- **History window:** send only the most recent **40** messages to the API. Full history remains
  in the DB and the UI; only the API payload is truncated.
- **Model:** update the default from `claude-sonnet-4-20250514` (deprecated) to
  `claude-sonnet-4-6`. `max_tokens` stays 1024.

### 4. Error handling

- Capped requests: 429 with a human-readable `detail`, raised before any Anthropic call.
- Existing behavior preserved: missing API key → 503; Anthropic errors → logged server-side,
  generic 502 to the client.

### 5. Testing

Backend tests with the Anthropic client call monkeypatched:

- Per-user cap trips at N user messages for the same callsign in a UTC day; another callsign is
  unaffected.
- Global cap trips independently of per-user counts (including NULL-callsign legacy rows).
- Limit value `0` disables the corresponding cap.
- Cap check happens before the API call (monkeypatched call not invoked on a capped request).
- History truncation: with >40 stored messages, the API payload contains only the last 40.
- `sender_callsign` is recorded on new user messages and round-trips through the API response
  path without breaking existing serializers.

## Scope of change

`chat_service.py`, the activities send-message route, one alembic migration, two AppConfig keys
(+ admin UI fields), a small frontend error display tweak, and tests. No pagination, no new
pages, no background jobs.
