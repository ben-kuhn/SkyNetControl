# Activities Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `/activities` page so net control can browse the activity library, create/edit/delete activities, and brainstorm new activities via a Claude-powered chat.

**Architecture:** Frontend-only — the backend has full CRUD plus chat-session endpoints. The page is a two-pane responsive layout: library table on the left, contextual right pane that shows either an activity detail panel (view/edit/create) or the brainstorm chat panel. On smaller viewports, the chat falls back to a fullscreen modal. Chat sessions are ephemeral per page-load.

**Tech Stack:** React, TypeScript, Tailwind CSS, React Router.

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/api/activities.ts` | API client: activity CRUD + tag list + chat (start, send, approve) |
| `frontend/src/pages/ActivitiesPage.tsx` | Top-level page: header + library table + right-pane router |
| `frontend/src/pages/activities/ActivityDetailPanel.tsx` | View / edit / create modes for a single activity |
| `frontend/src/pages/activities/BrainstormPanel.tsx` | Chat UI, composer, approval form, responsive variant |

**Modified files:**

| File | Change |
|------|--------|
| `frontend/src/types/index.ts` | Add `Activity`, `ActivityTag`, `ChatMessage`, `ChatMessageRole`, `ChatSession` types |
| `frontend/src/App.tsx` | Replace `PlaceholderPage title="Activities"` with `<ActivitiesPage />`; widen `minRole` to `["net_control", "admin"]` |

---

### Task 1: Frontend types

**Files:**
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Append types**

Append to `frontend/src/types/index.ts`:

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

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat: add Activity and ChatSession types"
```

---

### Task 2: API client

**Files:**
- Create: `frontend/src/api/activities.ts`

- [ ] **Step 1: Create the client**

Create `frontend/src/api/activities.ts`:

```typescript
import { apiFetch } from "./client";
import type { Activity, ActivityTag, ChatMessage, ChatSession } from "../types";

export interface ActivityInput {
  title: string;
  description: string;
  instructions: string;
  tag_names: string[];
}

export async function fetchActivities(): Promise<Activity[]> {
  return apiFetch<Activity[]>("/activities/");
}

export async function fetchActivity(id: number): Promise<Activity> {
  return apiFetch<Activity>(`/activities/${id}`);
}

export async function createActivity(input: ActivityInput): Promise<Activity> {
  return apiFetch<Activity>("/activities/", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateActivity(
  id: number,
  input: Partial<ActivityInput>,
): Promise<Activity> {
  return apiFetch<Activity>(`/activities/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteActivity(id: number): Promise<void> {
  await apiFetch<void>(`/activities/${id}`, { method: "DELETE" });
}

export async function fetchActivityTags(): Promise<ActivityTag[]> {
  return apiFetch<ActivityTag[]>("/activities/tags");
}

// --- Chat ---

export async function startChatSession(): Promise<ChatSession> {
  return apiFetch<ChatSession>("/activities/chat/sessions", { method: "POST" });
}

export async function sendChatMessage(
  sessionId: number,
  content: string,
): Promise<{ user_message: ChatMessage; assistant_message: ChatMessage }> {
  return apiFetch(`/activities/chat/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function approveChatSession(
  sessionId: number,
  input: ActivityInput,
): Promise<Activity> {
  return apiFetch<Activity>(`/activities/chat/sessions/${sessionId}/approve`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/activities.ts
git commit -m "feat: add activities API client"
```

---

### Task 3: ActivityDetailPanel component

**Files:**
- Create: `frontend/src/pages/activities/ActivityDetailPanel.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/pages/activities/ActivityDetailPanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import {
  createActivity,
  deleteActivity,
  updateActivity,
  type ActivityInput,
} from "../../api/activities";
import type { Activity } from "../../types";
import { useToast } from "../../context/ToastContext";
import { useAuth } from "../../hooks/useAuth";

function formatLongDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

type Mode = "view" | "edit" | "create";

interface Props {
  // When `activity` is non-null we render view/edit modes for it.
  // When `activity` is null we render create mode.
  activity: Activity | null;
  initialMode: Mode;
  onClose: () => void;
  onSaved: (a: Activity) => void;
  onDeleted: (id: number) => void;
  hasUnsavedRef?: { current: boolean };
}

export function ActivityDetailPanel({
  activity,
  initialMode,
  onClose,
  onSaved,
  onDeleted,
  hasUnsavedRef,
}: Props) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [title, setTitle] = useState(activity?.title ?? "");
  const [description, setDescription] = useState(activity?.description ?? "");
  const [instructions, setInstructions] = useState(activity?.instructions ?? "");
  const [tagsText, setTagsText] = useState(
    activity?.tags.map((t) => t.name).join(", ") ?? "",
  );
  const [submitting, setSubmitting] = useState(false);

  const { user } = useAuth();
  const { addToast } = useToast();

  useEffect(() => {
    // When the parent swaps in a different activity, reset form state.
    setMode(initialMode);
    setTitle(activity?.title ?? "");
    setDescription(activity?.description ?? "");
    setInstructions(activity?.instructions ?? "");
    setTagsText(activity?.tags.map((t) => t.name).join(", ") ?? "");
  }, [activity?.id, initialMode]);

  // Track unsaved state for the parent (used by the brainstorm flow to prompt before discarding).
  useEffect(() => {
    if (!hasUnsavedRef) return;
    if (mode === "view") {
      hasUnsavedRef.current = false;
      return;
    }
    const dirty =
      title !== (activity?.title ?? "") ||
      description !== (activity?.description ?? "") ||
      instructions !== (activity?.instructions ?? "") ||
      tagsText !== (activity?.tags.map((t) => t.name).join(", ") ?? "");
    hasUnsavedRef.current = dirty;
  }, [mode, title, description, instructions, tagsText, activity, hasUnsavedRef]);

  const canDelete = user?.role === "admin" && activity != null && !activity.is_default;
  const canEdit =
    activity == null ||
    user?.role === "admin" ||
    user?.role === "net_control";

  const parseTags = (s: string): string[] => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const part of s.split(",")) {
      const t = part.trim();
      if (t && !seen.has(t.toLowerCase())) {
        seen.add(t.toLowerCase());
        out.push(t);
      }
    }
    return out;
  };

  const handleSave = async () => {
    if (!title.trim() || !description.trim() || !instructions.trim()) {
      addToast("Title, description, and instructions are required.", "error");
      return;
    }
    setSubmitting(true);
    const input: ActivityInput = {
      title: title.trim(),
      description,
      instructions,
      tag_names: parseTags(tagsText),
    };
    try {
      const saved = activity
        ? await updateActivity(activity.id, input)
        : await createActivity(input);
      addToast(activity ? "Activity updated." : "Activity created.", "success");
      onSaved(saved);
      setMode("view");
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Save failed", "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!activity || activity.is_default) return;
    if (!confirm(`Delete activity "${activity.title}"?`)) return;
    try {
      await deleteActivity(activity.id);
      addToast("Activity deleted.", "success");
      onDeleted(activity.id);
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Delete failed", "error");
    }
  };

  return (
    <div className="border border-border rounded-lg p-4 bg-bg-surface">
      <div className="flex items-start justify-between mb-3 pb-3 border-b border-border">
        <div>
          <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
            {mode === "create" ? "New activity" : activity?.title ?? ""}
            {activity?.is_default && (
              <span className="inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium bg-accent/[0.12] text-accent">
                default
              </span>
            )}
          </h2>
          {activity && mode === "view" && (
            <p className="text-xs text-text-muted mt-0.5">
              Created {formatLongDate(activity.created_at)}
              {activity.last_used_at && ` · Last used ${formatLongDate(activity.last_used_at)}`}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary p-1 rounded"
          aria-label="Close detail panel"
        >
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {mode === "view" && activity && (
        <>
          {activity.tags.length > 0 && (
            <div className="mb-3 flex flex-wrap gap-1">
              {activity.tags.map((t) => (
                <span
                  key={t.id}
                  className="inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium bg-bg-elevated text-text-secondary"
                >
                  {t.name}
                </span>
              ))}
            </div>
          )}
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Description</label>
            <p className="text-sm text-text-primary whitespace-pre-wrap">{activity.description}</p>
          </div>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Instructions</label>
            <pre className="text-[0.8125rem] text-text-primary whitespace-pre-wrap font-mono bg-bg-elevated/50 p-3 rounded">
              {activity.instructions}
            </pre>
          </div>
          <div className="flex gap-2 pt-3 border-t border-border">
            {canEdit && (
              <button
                onClick={() => setMode("edit")}
                className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
              >
                Edit
              </button>
            )}
            <button
              onClick={handleDelete}
              disabled={!canDelete}
              className="px-3 py-1.5 text-sm border border-warning/40 rounded-md text-warning hover:bg-warning/[0.08] disabled:opacity-40 disabled:hover:bg-transparent"
              title={
                activity.is_default
                  ? "Cannot delete the default activity"
                  : user?.role !== "admin"
                    ? "Only admins can delete activities"
                    : "Delete"
              }
            >
              Delete
            </button>
          </div>
        </>
      )}

      {(mode === "edit" || mode === "create") && (
        <>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Tags</label>
            <input
              type="text"
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              placeholder="Comma-separated tags"
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={4}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Instructions</label>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={10}
              className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
            />
          </div>
          <div className="flex gap-2 pt-3 border-t border-border">
            <button
              onClick={handleSave}
              disabled={submitting}
              className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => (mode === "create" ? onClose() : setMode("view"))}
              className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
            >
              Cancel
            </button>
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/activities/ActivityDetailPanel.tsx
git commit -m "feat: add ActivityDetailPanel with view/edit/create modes"
```

---

### Task 4: BrainstormPanel component

**Files:**
- Create: `frontend/src/pages/activities/BrainstormPanel.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/pages/activities/BrainstormPanel.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import {
  approveChatSession,
  sendChatMessage,
  startChatSession,
  type ActivityInput,
} from "../../api/activities";
import type { Activity, ChatMessage } from "../../types";
import { useToast } from "../../context/ToastContext";

interface Props {
  onClose: () => void;
  onApproved: (a: Activity) => void;
  /** When true, render as a fullscreen modal (mobile). Otherwise inline pane. */
  modal: boolean;
}

export function BrainstormPanel({ onClose, onApproved, modal }: Props) {
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [composer, setComposer] = useState("");
  const [sending, setSending] = useState(false);
  const [apiKeyMissing, setApiKeyMissing] = useState(false);
  const [showApprove, setShowApprove] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Approval form state
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [tagsText, setTagsText] = useState("");

  const transcriptRef = useRef<HTMLDivElement>(null);
  const { addToast } = useToast();

  useEffect(() => {
    let cancelled = false;
    startChatSession()
      .then((s) => {
        if (!cancelled) setSessionId(s.id);
      })
      .catch((e: any) => {
        if (!cancelled) {
          addToast(e?.detail ?? e?.message ?? "Failed to start chat", "error");
          onClose();
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Scroll the transcript to the bottom when messages change.
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight });
  }, [messages.length]);

  const handleSend = async () => {
    if (!sessionId || !composer.trim() || sending) return;
    const text = composer;
    setComposer("");
    setSending(true);
    try {
      const { user_message, assistant_message } = await sendChatMessage(sessionId, text);
      setMessages((prev) => [...prev, user_message, assistant_message]);
      setApiKeyMissing(false);
    } catch (e: any) {
      if (e?.status === 503) {
        setApiKeyMissing(true);
      } else {
        addToast(e?.detail ?? e?.message ?? "Failed to send", "error");
        setComposer(text); // restore so the user doesn't lose their input
      }
    } finally {
      setSending(false);
    }
  };

  const parseTags = (s: string): string[] => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const part of s.split(",")) {
      const t = part.trim();
      if (t && !seen.has(t.toLowerCase())) {
        seen.add(t.toLowerCase());
        out.push(t);
      }
    }
    return out;
  };

  const handleApprove = async () => {
    if (!sessionId) return;
    if (!title.trim() || !description.trim() || !instructions.trim()) {
      addToast("Title, description, and instructions are required.", "error");
      return;
    }
    setSubmitting(true);
    const input: ActivityInput = {
      title: title.trim(),
      description,
      instructions,
      tag_names: parseTags(tagsText),
    };
    try {
      const activity = await approveChatSession(sessionId, input);
      addToast("Activity created from chat.", "success");
      onApproved(activity);
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Failed to save activity", "error");
    } finally {
      setSubmitting(false);
    }
  };

  const hasAssistant = messages.some((m) => m.role === "assistant");

  const containerCls = modal
    ? "fixed inset-0 z-50 bg-bg-base p-4 flex flex-col"
    : "border border-border rounded-lg bg-bg-surface flex flex-col h-[calc(100vh-8rem)] max-h-[800px]";

  return (
    <div className={containerCls}>
      <div className="flex items-center justify-between pb-3 mb-3 border-b border-border px-1">
        <h2 className="text-lg font-semibold text-text-primary">Brainstorm activity</h2>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary p-1 rounded"
          aria-label="Close brainstorm"
        >
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {apiKeyMissing && (
        <div className="mb-3 px-3 py-2 rounded border border-warning/40 bg-warning/[0.08] text-warning text-sm">
          Claude API key not configured. Visit <a href="/config" className="underline">/config</a> to set it.
        </div>
      )}

      <div
        ref={transcriptRef}
        className="flex-1 overflow-auto border border-border rounded-lg p-3 bg-bg-elevated/30 mb-3 space-y-2"
      >
        {messages.length === 0 && (
          <p className="text-text-muted text-sm text-center py-8">
            Tell Claude what kind of activity you want to design.
          </p>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] px-3 py-2 rounded-lg whitespace-pre-wrap text-sm ${
                m.role === "user"
                  ? "bg-accent/[0.15] text-text-primary"
                  : "bg-bg-elevated text-text-primary"
              }`}
            >
              {m.content}
            </div>
          </div>
        ))}
      </div>

      <div className="flex gap-2 mb-3">
        <textarea
          value={composer}
          onChange={(e) => setComposer(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          disabled={sending || apiKeyMissing}
          rows={2}
          placeholder="Type a message…"
          className="flex-1 px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary disabled:opacity-60"
        />
        <button
          onClick={handleSend}
          disabled={!composer.trim() || sending || apiKeyMissing}
          className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </div>

      {hasAssistant && !showApprove && (
        <div className="pt-3 border-t border-border">
          <button
            onClick={() => setShowApprove(true)}
            className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
          >
            Save as activity
          </button>
        </div>
      )}

      {showApprove && (
        <div className="pt-3 border-t border-border">
          <h3 className="text-sm font-semibold text-text-primary mb-2">Save chat as activity</h3>
          <div className="mb-2">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-2">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Tags</label>
            <input
              type="text"
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              placeholder="Comma-separated tags"
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-2">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-2">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Instructions</label>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={6}
              className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleApprove}
              disabled={submitting}
              className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => setShowApprove(false)}
              className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/activities/BrainstormPanel.tsx
git commit -m "feat: add BrainstormPanel for Claude-chat activity authoring"
```

---

### Task 5: ActivitiesPage shell + library table

**Files:**
- Create: `frontend/src/pages/ActivitiesPage.tsx`

- [ ] **Step 1: Create the page**

Create `frontend/src/pages/ActivitiesPage.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchActivities } from "../api/activities";
import type { Activity } from "../types";
import { ActivityDetailPanel } from "./activities/ActivityDetailPanel";
import { BrainstormPanel } from "./activities/BrainstormPanel";

type SortKey = "title" | "last_used_at";
type SortDir = "asc" | "desc";
type RightPane =
  | { kind: "empty" }
  | { kind: "detail"; activityId: number | null; mode: "view" | "edit" | "create" }
  | { kind: "brainstorm" };

function formatShortDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function ActivitiesPage() {
  const [activities, setActivities] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("title");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [pane, setPane] = useState<RightPane>({ kind: "empty" });

  // Tracks whether the detail panel has unsaved edits — used when opening brainstorm.
  const detailUnsavedRef = useRef(false);

  useEffect(() => {
    fetchActivities()
      .then((data) => setActivities(data))
      .catch((e: any) => setError(e?.message ?? "Failed to load activities"))
      .finally(() => setLoading(false));
  }, []);

  const sorted = useMemo(() => {
    const cmp = (a: Activity, b: Activity): number => {
      if (sortKey === "title") {
        const r = a.title.localeCompare(b.title);
        return sortDir === "asc" ? r : -r;
      }
      // last_used_at — nulls last for asc, first for desc
      const av = a.last_used_at ?? "";
      const bv = b.last_used_at ?? "";
      if (av === bv) return 0;
      const r = av < bv ? -1 : 1;
      return sortDir === "asc" ? r : -r;
    };
    return [...activities].sort(cmp);
  }, [activities, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("asc");
    }
  };
  const sortIndicator = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  const selectedActivity =
    pane.kind === "detail" && pane.activityId !== null
      ? activities.find((a) => a.id === pane.activityId) ?? null
      : null;

  const openDetail = (a: Activity) => {
    if (pane.kind === "detail" && pane.activityId === a.id) {
      setPane({ kind: "empty" });
    } else {
      setPane({ kind: "detail", activityId: a.id, mode: "view" });
    }
  };

  const openCreate = () => {
    setPane({ kind: "detail", activityId: null, mode: "create" });
  };

  const openBrainstorm = () => {
    if (
      pane.kind === "detail" &&
      detailUnsavedRef.current &&
      !confirm("Discard your unsaved edits and start a brainstorm?")
    ) {
      return;
    }
    setPane({ kind: "brainstorm" });
  };

  const handleSaved = (saved: Activity) => {
    setActivities((prev) => {
      const idx = prev.findIndex((a) => a.id === saved.id);
      if (idx === -1) return [saved, ...prev];
      return prev.map((a) => (a.id === saved.id ? saved : a));
    });
    setPane({ kind: "detail", activityId: saved.id, mode: "view" });
  };

  const handleDeleted = (id: number) => {
    setActivities((prev) => prev.filter((a) => a.id !== id));
    setPane({ kind: "empty" });
  };

  const isMobile = typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-text-primary">Activities</h1>
        <div className="flex gap-2">
          <button
            onClick={openCreate}
            className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
          >
            + New activity
          </button>
          <button
            onClick={openBrainstorm}
            className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90"
          >
            + Brainstorm new activity
          </button>
        </div>
      </div>

      {loading && <p className="text-text-muted text-sm py-4">Loading…</p>}
      {error && <p className="text-error text-sm py-4">{error}</p>}

      {!loading && !error && (
        <div className="flex flex-col lg:flex-row gap-4">
          <div className="flex-1 min-w-0">
            <div className="border border-border rounded-lg overflow-auto">
              <table className="w-full text-[0.8125rem] border-collapse">
                <thead className="bg-bg-elevated">
                  <tr>
                    <th
                      onClick={() => toggleSort("title")}
                      className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border cursor-pointer select-none hover:text-text-primary"
                    >
                      Title{sortIndicator("title")}
                    </th>
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">
                      Tags
                    </th>
                    <th
                      onClick={() => toggleSort("last_used_at")}
                      className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border cursor-pointer select-none hover:text-text-primary"
                    >
                      Last used{sortIndicator("last_used_at")}
                    </th>
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">
                      Default
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((a) => {
                    const isSelected =
                      pane.kind === "detail" && pane.activityId === a.id;
                    return (
                      <tr
                        key={a.id}
                        onClick={() => openDetail(a)}
                        className={`border-b border-border last:border-b-0 cursor-pointer transition-colors ${
                          isSelected
                            ? "bg-accent/[0.08] border-l-2 border-l-accent"
                            : "hover:bg-bg-elevated/50"
                        }`}
                      >
                        <td className="px-3 py-2.5 font-semibold text-text-primary">
                          {a.title}
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="flex flex-wrap gap-1">
                            {a.tags.map((t) => (
                              <span
                                key={t.id}
                                className="inline-block text-[0.6875rem] px-1.5 py-0.5 rounded-full font-medium bg-bg-elevated text-text-secondary"
                              >
                                {t.name}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-3 py-2.5 text-text-secondary tabular-nums">
                          {formatShortDate(a.last_used_at)}
                        </td>
                        <td className="px-3 py-2.5">
                          {a.is_default && (
                            <span className="inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium bg-accent/[0.12] text-accent">
                              default
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {sorted.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-3 py-8 text-center text-text-muted text-sm">
                        No activities yet. Create one or start a brainstorm.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {pane.kind === "detail" && (
            <div className="flex-1 min-w-0">
              <ActivityDetailPanel
                activity={selectedActivity}
                initialMode={pane.mode}
                onClose={() => setPane({ kind: "empty" })}
                onSaved={handleSaved}
                onDeleted={handleDeleted}
                hasUnsavedRef={detailUnsavedRef}
              />
            </div>
          )}

          {pane.kind === "brainstorm" && !isMobile && (
            <div className="flex-1 min-w-0">
              <BrainstormPanel
                modal={false}
                onClose={() => setPane({ kind: "empty" })}
                onApproved={(a) => {
                  setActivities((prev) => [a, ...prev]);
                  setPane({ kind: "detail", activityId: a.id, mode: "view" });
                }}
              />
            </div>
          )}

          {pane.kind === "brainstorm" && isMobile && (
            <BrainstormPanel
              modal={true}
              onClose={() => setPane({ kind: "empty" })}
              onApproved={(a) => {
                setActivities((prev) => [a, ...prev]);
                setPane({ kind: "detail", activityId: a.id, mode: "view" });
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ActivitiesPage.tsx
git commit -m "feat: add ActivitiesPage with library table and right-pane router"
```

---

### Task 6: Wire route + widen access

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Update the route**

In `frontend/src/App.tsx`, find this line:

```tsx
<Route path="/activities" element={<ProtectedRoute minRole={["admin"] as UserRole[]}><PlaceholderPage title="Activities" /></ProtectedRoute>} />
```

Add this import near the other page imports:

```tsx
import { ActivitiesPage } from "./pages/ActivitiesPage";
```

Replace the route line with:

```tsx
<Route path="/activities" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><ActivitiesPage /></ProtectedRoute>} />
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: wire /activities route and widen access to net_control"
```

---

### Task 7: Full verification

- [ ] **Step 1: Run the complete backend test suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass (backend unchanged, just sanity-check no regressions).

- [ ] **Step 2: Verify frontend type-checks**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Manual UI verification (cannot be scripted)**

Sign in as admin (or net_control), then:

**Library:**
- Visit `/activities` → table shows the default activity ("Standard Winlink Check-in" or similar).
- Click `+ New activity` → form opens in the right pane with empty fields.
- Fill in title, description, instructions, tags → click `Save` → new row appears, panel switches to view mode.
- Click a row → detail panel shows view mode with description and instructions.
- Click `Edit` → form mode → modify → `Save` → updated row.
- Click `Delete` on a non-default activity → confirm → row removed.
- Try `Delete` on the default activity → button is disabled.
- As net_control: `Delete` button is disabled for everything.

**Brainstorm:**
- Click `+ Brainstorm new activity` → right pane (desktop) or fullscreen modal (mobile/<lg) opens with empty chat.
- With Claude API key configured: type a message → see assistant reply → click `Save as activity` → form expands → fill fields → `Save` → new activity appears at the top of the table, view mode opens.
- With Claude API key unset: send a message → see "Claude API key not configured" banner; Send button disabled.
- Close the chat panel → state cleared.
- Open the detail panel in edit mode with unsaved changes, then click `+ Brainstorm` → confirm prompt fires.

Report any UI issues; do not claim the task complete without performing this verification.
