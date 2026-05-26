# Activities Page Design

## Overview

A page at `/activities` that lets net control browse the activity library, create/edit/delete activities, and brainstorm new activities via a Claude-powered chat. Closes the last spec gap — "Module 2: Activities" frontend.

## Scope

Frontend-only. The backend has full CRUD, tag listing, chat sessions, send-message, and approve-from-chat endpoints. The page uses them as-is.

## Access

`/activities` is currently `minRole={["admin"]}`. Widen to `["net_control", "admin"]` to match the backend create/update endpoints (which accept both roles). Delete remains admin-only at the backend, so the UI disables the Delete button for non-admins (and the backend enforces it as defense-in-depth).

## Layout

Single page, two-pane responsive layout (same pattern as Members / CheckIns / Roster):

- **Left pane**: library table.
- **Right pane (lg+)**: contextual slot — empty state, activity detail panel, or chat brainstorm panel.
- **Smaller viewports**: panes stack. The activity detail renders inline below the table. The chat opens as a fullscreen modal overlay.
- Above the table: title plus two buttons — `+ New activity` and `+ Brainstorm new activity`.

Only one of {detail panel, chat panel} is active in the right pane at a time. Opening the chat clears any current detail selection; opening a detail by row-click closes any open chat (with a confirm prompt if the chat has any messages, to avoid accidental loss).

## Library table

Columns:

| Column | Source | Notes |
|--------|--------|-------|
| Title | `title` | Monospace? No — plain text, bold. |
| Tags | `tags[].name` | Comma-separated chips (or compact pills). Wraps to next line if long. |
| Last used | `last_used_at` | Short date or em-dash if null. |
| Default | `is_default` | Small "default" pill when true. |
| Actions | — | Edit / Delete buttons (Delete disabled when `is_default` OR user role is not admin). |

Behavior:
- Sortable column headers (default: title asc).
- Row click toggles selection (opens / closes detail panel in view mode).
- Empty state: "No activities yet. Create one or start a brainstorm."

No client-side search in v1 (library size is small — a dozen or so activities). Sort + scroll is enough.

## Activity detail panel

Three modes: **view**, **edit**, **create**. The panel sub-component owns its own form state.

### View mode (opened by clicking a row)

- Header: title + (default badge if applicable) + close button.
- Tag chips.
- "Description" label + body.
- "Instructions" label + body in a monospace block (preserves whitespace and newlines).
- Metadata line: `Created {date} · Last used {date or "never"}`.
- Buttons row: `Edit` · `Delete` (admin-only and not default).

### Edit mode (clicking Edit from view, or via the row's Edit action)

- Title input (required).
- Tags input: a single text field with a placeholder like "Comma-separated tags". The string is split on `,`, trimmed, and de-duplicated before submit. Existing tags pre-populate as the joined string.
- Description textarea (required, 4 rows).
- Instructions textarea (required, monospace, 10 rows).
- Buttons row: `Save` (calls PATCH) · `Cancel` (returns to view mode).
- On save success, panel returns to view mode with the updated data.

### Create mode (opened via `+ New activity`)

Same form as edit, all fields empty. `Save` calls POST. On success, the new activity is selected and the panel switches to view mode for the new row.

### Delete

Button shown only in view mode. Confirm dialog ("Delete activity {title}?"). On confirm, calls DELETE. On 204, the row vanishes from the library and the panel closes. 403 surfaces as a toast.

## Brainstorm chat panel

Opening:
- `+ Brainstorm new activity` button calls `POST /api/activities/chat/sessions`, stores the returned `chat_session_id` in state, and opens the panel.
- On lg+ viewports: takes the right-pane slot, replacing any open detail panel.
- On smaller viewports: renders as a fullscreen modal overlay (`fixed inset-0 z-50 bg-bg-base p-4`).
- If the open detail panel is in edit/create mode with unsaved changes, prompt before discarding ("Discard your unsaved edits and start a brainstorm?"). View mode requires no prompt.

Layout (top to bottom):

1. **Header**: "Brainstorm activity" + close button. Closing the panel discards the chat session reference (the backend record persists but the UI doesn't surface it again).
2. **Transcript**: scrollable list of messages. User messages right-aligned with accent background; assistant messages left-aligned with elevated background. Preserve newlines (white-space: pre-wrap), plain text only — no markdown rendering in v1.
3. **Composer**: textarea + `Send` button. Enter sends, Shift+Enter inserts a newline. Disabled while a send is in flight.
4. **Approval CTA** (visible only after at least one assistant message exists): a `Save as activity` button. Clicking expands an inline approval form below the composer.

### Approval form

Fields (same as the activity edit form):
- Title (required)
- Tags (comma-separated)
- Description (required)
- Instructions (required, monospace)

Initially blank — the user copies relevant content from the chat into these fields by hand. (No automatic extraction in v1; that's a smarter UX feature for later.)

Buttons: `Save` (calls `POST /api/activities/chat/sessions/{id}/approve`) · `Cancel` (collapses the form, returns to chatting).

On success: panel closes, library refreshes, the new activity is selected, view mode opens in the right pane.

### Error states

- **503 on send** ("Claude API key not configured"): render an inline banner at the top of the chat area: "Claude API key not configured. Visit /config to set it." The composer disables the Send button.
- **502 on send** ("Claude API error"): inline error chip below the failed message with a retry affordance? In v1: just a toast with the error detail.
- **Network error**: toast with a generic "Failed to send message — try again."

## Backend

**No changes.** Existing endpoints in `backend/modules/activities/routes.py`:

- `POST /api/activities/` — create
- `GET /api/activities/` — list
- `GET /api/activities/tags` — list tags (used to autocomplete? In v1 we won't — the tag input is freeform text. Endpoint remains available for future use.)
- `GET /api/activities/{id}` — get one
- `PATCH /api/activities/{id}` — update
- `DELETE /api/activities/{id}` — delete (admin only)
- `POST /api/activities/chat/sessions` — start a chat
- `GET /api/activities/chat/sessions/{id}` — fetch session with history (unused by the page in v1 since chats are ephemeral; available for future)
- `POST /api/activities/chat/sessions/{id}/messages` — send a user message, return both messages
- `POST /api/activities/chat/sessions/{id}/approve` — create activity from chat

## Frontend file structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/api/activities.ts` | API client: activity CRUD + tag list + chat (create session, send, approve) |
| `frontend/src/pages/ActivitiesPage.tsx` | Shell — header, library table, right-pane router (empty/detail/chat) |
| `frontend/src/pages/activities/ActivityDetailPanel.tsx` | View/edit/create modes |
| `frontend/src/pages/activities/BrainstormPanel.tsx` | Chat UI + approval form, with responsive variant (right pane on lg, modal on smaller) |

**Modified files:**

| File | Change |
|------|--------|
| `frontend/src/types/index.ts` | Add `Activity`, `ActivityTag`, `ChatMessage`, `ChatMessageRole`, `ChatSession` types |
| `frontend/src/App.tsx` | Replace `PlaceholderPage title="Activities"` with `<ActivitiesPage />`; widen `minRole` to `["net_control", "admin"]` |

## Types

```typescript
export interface ActivityTag {
  id: number;
  name: string;
}

export interface Activity {
  id: number;
  title: string;
  description: string;
  instructions: string;
  is_default: boolean;
  created_at: string;
  last_used_at: string | null;
  tags: ActivityTag[];
}

export type ChatMessageRole = "user" | "assistant";

export interface ChatMessage {
  id: number;
  role: ChatMessageRole;
  content: string;
  created_at: string;
}

export interface ChatSession {
  id: number;
  activity_id: number | null;
  created_at: string;
  messages: ChatMessage[];
}
```

## API client

`frontend/src/api/activities.ts` exposes:

- `fetchActivities(): Promise<Activity[]>`
- `fetchActivity(id): Promise<Activity>`
- `createActivity({title, description, instructions, tag_names}): Promise<Activity>`
- `updateActivity(id, partial): Promise<Activity>`
- `deleteActivity(id): Promise<void>`
- `fetchActivityTags(): Promise<ActivityTag[]>` (exposed for future use)
- `startChatSession(): Promise<ChatSession>`
- `sendChatMessage(sessionId, content): Promise<{user_message: ChatMessage, assistant_message: ChatMessage}>`
- `approveChat(sessionId, {title, description, instructions, tag_names}): Promise<Activity>`

## Data flow

**Page mount:** `fetchActivities()` populates the table. No tags fetched until/unless we wire autocomplete (out of scope).

**Selecting a row:** Pure UI state. The selected activity's full data is already in the list response; no extra fetch needed unless we want fresh data — skip for v1.

**Saving an edit:** PATCH → on success, replace the row in state with the response.

**Brainstorm flow:**
1. Click `+ Brainstorm` → `startChatSession()` → store ID in component state → render chat panel.
2. User types and hits Send → `sendChatMessage(id, text)` → append both messages to transcript state.
3. User clicks `Save as activity` → form expands → user fills fields → submit → `approveChat(id, fields)` → on success, prepend the new activity to the table state, close panel, select the new row.

**Closing the chat panel:** Just clear local state. The backend record stays.

## Error handling

- Toasts for save/delete/send errors.
- Inline banner in chat for 503 (missing API key).
- "Cannot delete the default activity" backend response surfaces as a toast (the button is also disabled to prevent the click).
- 403 on delete (net_control attempting) surfaces as a toast.

## Testing

Backend: no changes — no new tests required.

Frontend:
- `tsc --noEmit` passes.
- Manual UI verification:
  - Sign in as admin → /activities renders, library shows the default activity.
  - Click `+ New activity` → form opens → fill and Save → new row appears.
  - Click a row → detail panel shows. Click Edit → modify → Save → updated data shows.
  - Click Delete on a non-default activity → confirm → row vanishes.
  - Try to Delete the default activity → button disabled with tooltip.
  - Click `+ Brainstorm new activity` → chat panel opens. Send a message → see assistant reply.
  - Click `Save as activity` → fill form → submit → new activity appears in library.
  - Close chat mid-conversation → state cleared, no errors.
  - With Claude API key unset: send a message → see the "Claude API key not configured" banner.
  - On a narrow viewport (≤lg breakpoint), brainstorm opens as a fullscreen modal.

## Out of scope

- Markdown rendering in chat or instruction display.
- Persisted, resumable chat sessions (the backend stores them; UI never browses).
- Tag autocomplete (the `/tags` endpoint exists for later use).
- Streaming chat responses (the API is request/response).
- File uploads or attachments in chat.
- Activity usage history beyond the `last_used_at` field.
- Smart extraction of title/description/instructions from chat content.
- Tag deletion / rename (tags are managed implicitly via activity edits).
