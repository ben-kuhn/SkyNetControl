# Reminders Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `/reminders` page so net control can review, edit, approve, send, skip, and regenerate reminder drafts, plus manage reminder templates.

**Architecture:** Backend gains one new endpoint (`POST /api/reminders/{id}/regenerate`) and one service helper; everything else exists. Frontend gets a typed API client, a shell page with top tabs (Drafts / Templates), and two tab components — the Drafts tab uses status sub-tabs and a slide-in detail panel matching the CheckIns/Members pattern. Splitting the page into three files keeps each component focused.

**Tech Stack:** FastAPI, SQLAlchemy, Python, React/TypeScript, Tailwind CSS.

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/api/reminders.ts` | API client: list / edit / approve / send / skip / generate / regenerate reminders and CRUD templates |
| `frontend/src/pages/RemindersPage.tsx` | Top-level page; renders top tab switcher and mounts the active tab |
| `frontend/src/pages/reminders/DraftsTab.tsx` | Drafts list, status sub-tabs, detail panel, Generate modal |
| `frontend/src/pages/reminders/TemplatesTab.tsx` | Templates table + create/edit/delete modal |

**Modified files:**

| File | Change |
|------|--------|
| `backend/modules/reminders/service.py` | Add `regenerate_draft` |
| `backend/modules/reminders/routes.py` | Add `POST /{id}/regenerate` |
| `tests/test_reminder_service.py` | Tests for `regenerate_draft` |
| `tests/test_reminder_routes.py` | Tests for the new route |
| `frontend/src/types/index.ts` | Add `Reminder`, `ReminderStatus`, `ReminderTemplate`, `ReminderTemplateType` |
| `frontend/src/App.tsx` | Replace `PlaceholderPage title="Reminders"` with `<RemindersPage />` |

---

### Task 1: Backend service — `regenerate_draft`

**Files:**
- Modify: `backend/modules/reminders/service.py`
- Test: `tests/test_reminder_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reminder_service.py`:

```python
def test_regenerate_draft_rewrites_subject_and_body(db: Session, season_and_sessions):
    """regenerate_draft re-renders against the current session and template."""
    from backend.modules.reminders.service import regenerate_draft

    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Activity Default",
        template_type=TemplateType.ACTIVITY,
        subject_template="{{ activity_title }} — {{ date }}",
        body_template="Activity: {{ activity_title }}. Notes: {{ activity_instructions }}",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session2.id)
    assert log is not None

    # Hand-edit the draft body
    log.content_subject = "Edited subject"
    log.content_body = "Edited body"
    db.commit()

    result = regenerate_draft(db, log.id)
    assert result is not None
    assert result.id == log.id
    assert result.content_subject != "Edited subject"
    assert "Simplex Exercise" in result.content_subject
    assert "Tune to 146.520" in result.content_body  # from activity.instructions


def test_regenerate_draft_picks_up_activity_change(db: Session, season_and_sessions):
    """If the session's activity changes after generation, regenerate reflects it."""
    from backend.modules.reminders.service import regenerate_draft

    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Activity Default",
        template_type=TemplateType.ACTIVITY,
        subject_template="{{ activity_title }} — {{ date }}",
        body_template="Activity: {{ activity_title }}. {{ activity_instructions }}",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session2.id)
    assert log is not None
    assert "Simplex Exercise" in log.content_subject

    # Swap the activity on the session
    new_activity = Activity(
        title="Direction Finding",
        description="DF drill",
        instructions="Bring a directional antenna and a portable rig.",
    )
    db.add(new_activity)
    db.flush()
    session2.activity_id = new_activity.id
    db.commit()

    result = regenerate_draft(db, log.id)
    assert result is not None
    assert "Direction Finding" in result.content_subject
    assert "Bring a directional antenna" in result.content_body


def test_regenerate_draft_returns_none_when_not_draft(db: Session, season_and_sessions):
    """Approved/sent/skipped reminders can't be regenerated."""
    from backend.modules.reminders.service import regenerate_draft

    season, session1, _, _ = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net {{ date }}",
        body_template="Body",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    approve_reminder(db, log.id, approver_callsign="W0NE")

    result = regenerate_draft(db, log.id)
    assert result is None


def test_regenerate_draft_returns_none_when_missing(db: Session):
    from backend.modules.reminders.service import regenerate_draft

    assert regenerate_draft(db, 999) is None
```

The fixture `season_and_sessions` in `tests/test_reminder_service.py` already creates `session2` with an `activity_id` pointing at "Simplex Exercise" (with instructions "Tune to 146.520 and call CQ."). Reuse that.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_reminder_service.py::test_regenerate_draft_rewrites_subject_and_body tests/test_reminder_service.py::test_regenerate_draft_picks_up_activity_change tests/test_reminder_service.py::test_regenerate_draft_returns_none_when_not_draft tests/test_reminder_service.py::test_regenerate_draft_returns_none_when_missing -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with `ImportError: cannot import name 'regenerate_draft'`.

- [ ] **Step 3: Implement `regenerate_draft`**

In `backend/modules/reminders/service.py`, after the existing `update_draft` function, add:

```python
def regenerate_draft(db: Session, reminder_id: int) -> ReminderLog | None:
    """Re-render a DRAFT reminder against the current session + template.

    Returns the updated log, or None if the reminder is missing or not in DRAFT status.
    """
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status != ReminderStatus.DRAFT:
        return None

    net_session = db.get(NetSession, log.session_id)
    if net_session is None:
        return None

    template = None
    if log.template_id is not None:
        template = db.get(ReminderTemplate, log.template_id)
    if template is None:
        tmpl_type = _session_type_to_template_type(net_session.session_type)
        template = (
            db.query(ReminderTemplate)
            .filter(
                ReminderTemplate.template_type == tmpl_type,
                ReminderTemplate.is_default.is_(True),
            )
            .first()
        )
    if template is None:
        return None

    context = build_template_context(db, net_session)
    subject, body = render_reminder(template, context)
    log.content_subject = subject
    log.content_body = body
    db.commit()
    db.refresh(log)
    return log
```

Verify these names are already in scope at the top of the file: `ReminderLog`, `ReminderStatus`, `ReminderTemplate`, `NetSession`, `_session_type_to_template_type`, `build_template_context`, `render_reminder`. They are all used elsewhere in the file.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_reminder_service.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All reminder service tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/reminders/service.py tests/test_reminder_service.py
git commit -m "feat: add regenerate_draft service helper"
```

---

### Task 2: Backend route — `POST /api/reminders/{id}/regenerate`

**Files:**
- Modify: `backend/modules/reminders/routes.py`
- Test: `tests/test_reminder_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reminder_routes.py`:

```python
@pytest.mark.asyncio
async def test_regenerate_reminder_route_rewrites_draft(test_client, test_settings, db_setup):
    """Net control can regenerate a draft from the current session and template."""
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Stale subject",
            content_body="Stale body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.post(
        f"/api/reminders/{log_id}/regenerate",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == log_id
    assert data["status"] == "draft"
    # The rendered content should differ from the stale placeholder
    assert data["content_subject"] != "Stale subject"
    assert data["content_body"] != "Stale body"


@pytest.mark.asyncio
async def test_regenerate_reminder_route_404_when_missing(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.post(
        "/api/reminders/999/regenerate",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_regenerate_reminder_route_409_when_not_draft(test_client, test_settings, db_setup):
    """Approved reminders can't be regenerated."""
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.APPROVED,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
            approved_at=datetime.now(tz=timezone.utc),
            approved_by="W0NE",
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.post(
        f"/api/reminders/{log_id}/regenerate",
        cookies={"access_token": token},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_regenerate_reminder_route_requires_role(test_client, test_settings, db_setup):
    """Viewer cannot regenerate."""
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="S",
            content_body="B",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    viewer_token = create_access_token("KD0TST", "viewer", test_settings)
    resp = await test_client.post(
        f"/api/reminders/{log_id}/regenerate",
        cookies={"access_token": viewer_token},
    )
    assert resp.status_code == 403
```

If `db_setup` in this file doesn't already seed a `KD0TST` viewer user, add one to the fixture or pick a test pattern from `tests/test_checkin_routes.py:30-83` (where a viewer is seeded).

- [ ] **Step 2: Run tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_reminder_routes.py::test_regenerate_reminder_route_rewrites_draft tests/test_reminder_routes.py::test_regenerate_reminder_route_404_when_missing tests/test_reminder_routes.py::test_regenerate_reminder_route_409_when_not_draft tests/test_reminder_routes.py::test_regenerate_reminder_route_requires_role -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL — 404 (route doesn't exist yet).

- [ ] **Step 3: Add the route**

In `backend/modules/reminders/routes.py`:

Add `regenerate_draft` to the existing `from backend.modules.reminders.service import (...)` block.

Add the route handler near the other action routes (after the `skip` route around line 252):

```python
@reminders_router.post("/{reminder_id}/regenerate")
async def regenerate_reminder_route(
    reminder_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = regenerate_draft(db, reminder_id)
    if log is None:
        # Distinguish "missing" from "not in draft" by checking existence
        existing = db.get(ReminderLog, reminder_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Reminder not found")
        raise HTTPException(status_code=409, detail="Reminder not in draft status")
    return _reminder_to_response(log)
```

If `ReminderLog` isn't imported at the top of routes.py, add it to the existing model import block.

- [ ] **Step 4: Run tests and verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_reminder_routes.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass (including the four new tests).

- [ ] **Step 5: Run full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/reminders/routes.py tests/test_reminder_routes.py
git commit -m "feat: add POST /api/reminders/{id}/regenerate endpoint"
```

---

### Task 3: Frontend types

**Files:**
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Append the reminder types**

Append to `frontend/src/types/index.ts`:

```typescript
export type ReminderStatus = "draft" | "approved" | "sent" | "skipped";
export type ReminderTemplateType = "regular_checkin" | "activity";

export interface Reminder {
  id: number;
  session_id: number;
  template_id: number | null;
  status: ReminderStatus;
  content_subject: string;
  content_body: string;
  drafted_at: string;
  approved_at: string | null;
  sent_at: string | null;
  approved_by: string | null;
}

export interface ReminderTemplate {
  id: number;
  name: string;
  template_type: ReminderTemplateType;
  subject_template: string;
  body_template: string;
  lead_time_days: number;
  is_default: boolean;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat: add Reminder and ReminderTemplate types"
```

---

### Task 4: Frontend API client

**Files:**
- Create: `frontend/src/api/reminders.ts`

- [ ] **Step 1: Create the API client**

Create `frontend/src/api/reminders.ts`:

```typescript
import { apiFetch } from "./client";
import type { Reminder, ReminderTemplate, ReminderTemplateType } from "../types";

// --- Reminders ---

export async function fetchReminders(): Promise<Reminder[]> {
  return apiFetch<Reminder[]>("/reminders/");
}

export async function updateReminderDraft(
  id: number,
  body: { content_subject?: string; content_body?: string },
): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function approveReminder(id: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}/approve`, { method: "POST" });
}

export async function sendReminder(id: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}/send`, { method: "POST" });
}

export async function skipReminder(id: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}/skip`, { method: "POST" });
}

export async function regenerateReminderDraft(id: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}/regenerate`, { method: "POST" });
}

export async function generateReminderDraft(sessionId: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/generate/${sessionId}`, { method: "POST" });
}

// --- Templates ---

export async function fetchReminderTemplates(): Promise<ReminderTemplate[]> {
  return apiFetch<ReminderTemplate[]>("/reminders/templates");
}

export interface TemplateInput {
  name: string;
  template_type: ReminderTemplateType;
  subject_template: string;
  body_template: string;
  lead_time_days: number;
  is_default: boolean;
}

export async function createReminderTemplate(input: TemplateInput): Promise<ReminderTemplate> {
  return apiFetch<ReminderTemplate>("/reminders/templates", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateReminderTemplate(
  id: number,
  input: Partial<TemplateInput>,
): Promise<ReminderTemplate> {
  return apiFetch<ReminderTemplate>(`/reminders/templates/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteReminderTemplate(id: number): Promise<void> {
  await apiFetch<void>(`/reminders/templates/${id}`, { method: "DELETE" });
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/reminders.ts
git commit -m "feat: add reminders API client"
```

---

### Task 5: RemindersPage shell + wire into App.tsx

**Files:**
- Create: `frontend/src/pages/RemindersPage.tsx`
- Create: `frontend/src/pages/reminders/DraftsTab.tsx` (placeholder content)
- Create: `frontend/src/pages/reminders/TemplatesTab.tsx` (placeholder content)
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create the placeholder tab components**

Create `frontend/src/pages/reminders/DraftsTab.tsx`:

```tsx
export function DraftsTab() {
  return <p className="text-text-muted text-sm py-4">Drafts coming next…</p>;
}
```

Create `frontend/src/pages/reminders/TemplatesTab.tsx`:

```tsx
export function TemplatesTab() {
  return <p className="text-text-muted text-sm py-4">Templates coming next…</p>;
}
```

These placeholders let later tasks land independently without leaving broken imports.

- [ ] **Step 2: Create RemindersPage with tab switcher**

Create `frontend/src/pages/RemindersPage.tsx`:

```tsx
import { useState } from "react";
import { DraftsTab } from "./reminders/DraftsTab";
import { TemplatesTab } from "./reminders/TemplatesTab";

type TopTab = "drafts" | "templates";

export function RemindersPage() {
  const [tab, setTab] = useState<TopTab>("drafts");

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-text-primary mb-4">Reminders</h1>

      <div className="flex border-b border-border mb-4">
        <TabButton active={tab === "drafts"} onClick={() => setTab("drafts")}>Drafts</TabButton>
        <TabButton active={tab === "templates"} onClick={() => setTab("templates")}>Templates</TabButton>
      </div>

      {tab === "drafts" ? <DraftsTab /> : <TemplatesTab />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm border-b-2 transition-colors ${
        active
          ? "border-accent text-text-primary font-medium"
          : "border-transparent text-text-muted hover:text-text-primary"
      }`}
    >
      {children}
    </button>
  );
}
```

- [ ] **Step 3: Wire RemindersPage into App.tsx**

In `frontend/src/App.tsx`, add the import near the other page imports:

```tsx
import { RemindersPage } from "./pages/RemindersPage";
```

Replace the existing `/reminders` placeholder route. Find this line in the protected route group:

```tsx
<Route path="/reminders" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><PlaceholderPage title="Reminders" /></ProtectedRoute>} />
```

Replace with:

```tsx
<Route path="/reminders" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><RemindersPage /></ProtectedRoute>} />
```

- [ ] **Step 4: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/RemindersPage.tsx frontend/src/pages/reminders/DraftsTab.tsx frontend/src/pages/reminders/TemplatesTab.tsx frontend/src/App.tsx
git commit -m "feat: scaffold RemindersPage with tabs and wire route"
```

---

### Task 6: DraftsTab — list with status sub-tabs

**Files:**
- Modify: `frontend/src/pages/reminders/DraftsTab.tsx`

- [ ] **Step 1: Replace the placeholder with the list + sub-tabs**

Replace the contents of `frontend/src/pages/reminders/DraftsTab.tsx` with:

```tsx
import { useEffect, useMemo, useState } from "react";
import { fetchReminders } from "../../api/reminders";
import type { Reminder, ReminderStatus } from "../../types";

const STATUSES: ReminderStatus[] = ["draft", "approved", "sent", "skipped"];
const STATUS_LABEL: Record<ReminderStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  sent: "Sent",
  skipped: "Skipped",
};
const PILL_CLS: Record<ReminderStatus, string> = {
  draft: "bg-warning/[0.12] text-warning",
  approved: "bg-accent/[0.12] text-accent",
  sent: "bg-success/[0.12] text-success",
  skipped: "bg-text-muted/[0.12] text-text-muted",
};

function formatShortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function DraftsTab() {
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<ReminderStatus>("draft");

  useEffect(() => {
    fetchReminders()
      .then((data) => {
        setReminders(data);
        setError(null);
      })
      .catch((e) => setError(e?.message ?? "Failed to load reminders"))
      .finally(() => setLoading(false));
  }, []);

  const counts = useMemo(() => {
    const c: Record<ReminderStatus, number> = { draft: 0, approved: 0, sent: 0, skipped: 0 };
    for (const r of reminders) c[r.status]++;
    return c;
  }, [reminders]);

  const visible = useMemo(
    () => reminders.filter((r) => r.status === statusFilter),
    [reminders, statusFilter],
  );

  return (
    <div>
      <div className="flex gap-2 mb-3">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1.5 text-xs rounded-md border flex items-center gap-2 transition-colors ${
              statusFilter === s
                ? "bg-accent/[0.08] border-accent text-text-primary font-medium"
                : "bg-bg-elevated border-border text-text-muted hover:text-text-primary"
            }`}
          >
            {STATUS_LABEL[s]}
            <span
              className={`text-[0.6875rem] px-1.5 py-0.5 rounded ${
                statusFilter === s ? "bg-accent text-bg-base" : "bg-bg-base text-text-muted"
              }`}
            >
              {counts[s]}
            </span>
          </button>
        ))}
      </div>

      {loading && <p className="text-text-muted text-sm py-4">Loading…</p>}
      {error && <p className="text-error text-sm py-4">{error}</p>}

      {!loading && !error && (
        <div className="border border-border rounded-lg overflow-auto">
          <table className="w-full text-[0.8125rem] border-collapse">
            <thead className="bg-bg-elevated">
              <tr>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Session</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Subject</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-b-0 hover:bg-bg-elevated/50">
                  <td className="px-3 py-2.5 text-text-secondary font-variant-numeric tabular-nums">
                    {/* TODO Task 7: replace with actual session date */}
                    Session #{r.session_id}
                  </td>
                  <td className="px-3 py-2.5 text-text-primary">
                    <span className="block truncate max-w-md" title={r.content_subject}>
                      {r.content_subject}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${PILL_CLS[r.status]}`}>
                      {STATUS_LABEL[r.status]}
                    </span>
                  </td>
                </tr>
              ))}
              {visible.length === 0 && (
                <tr>
                  <td colSpan={3} className="px-3 py-8 text-center text-text-muted text-sm">
                    No {STATUS_LABEL[statusFilter].toLowerCase()} reminders.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

Note the `formatShortDate` helper is exported below (or rather kept here for Task 7's use). The `Session #{r.session_id}` placeholder in the row gets replaced in Task 7 once we have the sessions data.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/reminders/DraftsTab.tsx
git commit -m "feat: add DraftsTab list with status sub-tabs and counts"
```

---

### Task 7: DraftsTab — detail panel, session dates, actions

**Files:**
- Modify: `frontend/src/pages/reminders/DraftsTab.tsx`

- [ ] **Step 1: Replace the DraftsTab to add the detail panel and session date lookups**

Replace the entire contents of `frontend/src/pages/reminders/DraftsTab.tsx` with:

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  approveReminder,
  fetchReminders,
  sendReminder,
  skipReminder,
  updateReminderDraft,
} from "../../api/reminders";
import { fetchSessions } from "../../api/schedule";
import type { Reminder, ReminderStatus, Session } from "../../types";
import { useToast } from "../../context/ToastContext";

const STATUSES: ReminderStatus[] = ["draft", "approved", "sent", "skipped"];
const STATUS_LABEL: Record<ReminderStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  sent: "Sent",
  skipped: "Skipped",
};
const PILL_CLS: Record<ReminderStatus, string> = {
  draft: "bg-warning/[0.12] text-warning",
  approved: "bg-accent/[0.12] text-accent",
  sent: "bg-success/[0.12] text-success",
  skipped: "bg-text-muted/[0.12] text-text-muted",
};

function formatShortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatLongDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function DraftsTab() {
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<ReminderStatus>("draft");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const { addToast } = useToast();

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [rs, ss] = await Promise.all([fetchReminders(), fetchSessions()]);
      setReminders(rs);
      setSessions(ss);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const sessionById = useMemo(() => {
    const map = new Map<number, Session>();
    for (const s of sessions) map.set(s.id, s);
    return map;
  }, [sessions]);

  const counts = useMemo(() => {
    const c: Record<ReminderStatus, number> = { draft: 0, approved: 0, sent: 0, skipped: 0 };
    for (const r of reminders) c[r.status]++;
    return c;
  }, [reminders]);

  const visible = useMemo(
    () => reminders.filter((r) => r.status === statusFilter),
    [reminders, statusFilter],
  );

  const selected = selectedId ? reminders.find((r) => r.id === selectedId) ?? null : null;

  const onAction = async (action: () => Promise<Reminder>, success: string) => {
    try {
      const updated = await action();
      setReminders((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      addToast(success, "success");
    } catch (e: any) {
      const status = e?.status;
      if (status === 409 && action === (() => sendReminder(selectedId!))) {
        addToast("Send failed — verify delivery backends are configured (Config page).", "error");
      } else {
        addToast(e?.detail ?? e?.message ?? "Action failed", "error");
      }
      loadAll();
    }
  };

  return (
    <div>
      <div className="flex gap-2 mb-3">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1.5 text-xs rounded-md border flex items-center gap-2 transition-colors ${
              statusFilter === s
                ? "bg-accent/[0.08] border-accent text-text-primary font-medium"
                : "bg-bg-elevated border-border text-text-muted hover:text-text-primary"
            }`}
          >
            {STATUS_LABEL[s]}
            <span
              className={`text-[0.6875rem] px-1.5 py-0.5 rounded ${
                statusFilter === s ? "bg-accent text-bg-base" : "bg-bg-base text-text-muted"
              }`}
            >
              {counts[s]}
            </span>
          </button>
        ))}
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
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Session</th>
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Subject</th>
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((r) => {
                    const isSelected = r.id === selectedId;
                    const sess = sessionById.get(r.session_id);
                    return (
                      <tr
                        key={r.id}
                        onClick={() => setSelectedId(isSelected ? null : r.id)}
                        className={`border-b border-border last:border-b-0 cursor-pointer transition-colors ${
                          isSelected
                            ? "bg-accent/[0.08] border-l-2 border-l-accent"
                            : "hover:bg-bg-elevated/50"
                        }`}
                      >
                        <td className="px-3 py-2.5 text-text-secondary tabular-nums">
                          {sess ? formatShortDate(sess.start_date) : `#${r.session_id}`}
                        </td>
                        <td className="px-3 py-2.5 text-text-primary">
                          <span className="block truncate max-w-md" title={r.content_subject}>
                            {r.content_subject}
                          </span>
                        </td>
                        <td className="px-3 py-2.5">
                          <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${PILL_CLS[r.status]}`}>
                            {STATUS_LABEL[r.status]}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                  {visible.length === 0 && (
                    <tr>
                      <td colSpan={3} className="px-3 py-8 text-center text-text-muted text-sm">
                        No {STATUS_LABEL[statusFilter].toLowerCase()} reminders.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {selected && (
            <div className="flex-1 min-w-0">
              <DetailPanel
                reminder={selected}
                session={sessionById.get(selected.session_id) ?? null}
                onClose={() => setSelectedId(null)}
                onChanged={(updated) =>
                  setReminders((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
                }
                onError={(msg) => addToast(msg, "error")}
                onInfo={(msg) => addToast(msg, "success")}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DetailPanel({
  reminder,
  session,
  onClose,
  onChanged,
  onError,
  onInfo,
}: {
  reminder: Reminder;
  session: Session | null;
  onClose: () => void;
  onChanged: (r: Reminder) => void;
  onError: (msg: string) => void;
  onInfo: (msg: string) => void;
}) {
  const [subject, setSubject] = useState(reminder.content_subject);
  const [body, setBody] = useState(reminder.content_body);

  useEffect(() => {
    setSubject(reminder.content_subject);
    setBody(reminder.content_body);
  }, [reminder.id, reminder.content_subject, reminder.content_body]);

  const isDraft = reminder.status === "draft";
  const isApproved = reminder.status === "approved";

  const handleSave = async () => {
    try {
      const updated = await updateReminderDraft(reminder.id, {
        content_subject: subject,
        content_body: body,
      });
      onChanged(updated);
      onInfo("Draft saved.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Save failed");
    }
  };

  const handleApprove = async () => {
    try {
      const updated = await approveReminder(reminder.id);
      onChanged(updated);
      onInfo("Reminder approved.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Approve failed");
    }
  };

  const handleSend = async () => {
    try {
      const updated = await sendReminder(reminder.id);
      onChanged(updated);
      onInfo("Reminder sent.");
    } catch (e: any) {
      if (e?.status === 409) {
        onError("Send failed — verify delivery backends are configured (Config page).");
      } else {
        onError(e?.detail ?? e?.message ?? "Send failed");
      }
    }
  };

  const handleSkip = async () => {
    if (!confirm("Skip this reminder?")) return;
    try {
      const updated = await skipReminder(reminder.id);
      onChanged(updated);
      onInfo("Reminder skipped.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Skip failed");
    }
  };

  return (
    <div className="border border-border rounded-lg p-4 bg-bg-surface">
      <div className="flex items-start justify-between mb-3 pb-3 border-b border-border">
        <div>
          <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
            {session ? formatLongDate(session.start_date) : `Session #${reminder.session_id}`}
            <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${PILL_CLS[reminder.status]}`}>
              {STATUS_LABEL[reminder.status]}
            </span>
          </h2>
          <p className="text-xs text-text-muted mt-0.5">
            Drafted {formatLongDate(reminder.drafted_at)}
            {reminder.approved_at && ` · Approved ${formatLongDate(reminder.approved_at)} by ${reminder.approved_by}`}
            {reminder.sent_at && ` · Sent ${formatLongDate(reminder.sent_at)}`}
          </p>
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

      <div className="mb-3">
        <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Subject</label>
        <input
          type="text"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          disabled={!isDraft}
          className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary disabled:opacity-60"
        />
      </div>

      <div className="mb-3">
        <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Body</label>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          disabled={!isDraft}
          rows={12}
          className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono disabled:opacity-60"
        />
      </div>

      <div className="flex gap-2 flex-wrap pt-3 border-t border-border">
        {isDraft && (
          <>
            <button onClick={handleSave} className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90">
              Save draft
            </button>
            <button onClick={handleApprove} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
              Approve
            </button>
          </>
        )}
        {isApproved && (
          <button onClick={handleSend} className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90">
            Send
          </button>
        )}
        {(isDraft || isApproved) && (
          <button onClick={handleSkip} className="px-3 py-1.5 text-sm border border-warning/40 rounded-md text-warning hover:bg-warning/[0.08]">
            Skip
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/reminders/DraftsTab.tsx
git commit -m "feat: add DraftsTab detail panel with edit and actions"
```

---

### Task 8: DraftsTab — regenerate button + Generate-draft modal

**Files:**
- Modify: `frontend/src/pages/reminders/DraftsTab.tsx`

- [ ] **Step 1: Add the imports**

In `frontend/src/pages/reminders/DraftsTab.tsx`, update the imports block to include the new functions:

```tsx
import {
  approveReminder,
  fetchReminders,
  generateReminderDraft,
  regenerateReminderDraft,
  sendReminder,
  skipReminder,
  updateReminderDraft,
} from "../../api/reminders";
```

- [ ] **Step 2: Add the Generate-draft button + modal to the DraftsTab body**

In the `DraftsTab` component, add a state variable near the other state declarations:

```tsx
const [showGenerateModal, setShowGenerateModal] = useState(false);
```

Just above the status sub-tabs (before the `<div className="flex gap-2 mb-3">` row), add a button row:

```tsx
<div className="flex justify-end mb-2">
  <button
    onClick={() => setShowGenerateModal(true)}
    className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90"
  >
    + Generate draft
  </button>
</div>
```

At the end of the DraftsTab return (just before the final closing `</div>`), conditionally render the modal:

```tsx
{showGenerateModal && (
  <GenerateModal
    sessions={sessions.filter((s) => s.status === "scheduled")}
    onClose={() => setShowGenerateModal(false)}
    onGenerated={(generated) => {
      setReminders((prev) => {
        // If this session already had a reminder, replace it; otherwise prepend
        const exists = prev.some((r) => r.id === generated.id);
        return exists ? prev.map((r) => (r.id === generated.id ? generated : r)) : [generated, ...prev];
      });
      setStatusFilter("draft");
      setSelectedId(generated.id);
      setShowGenerateModal(false);
    }}
    onError={(msg) => addToast(msg, "error")}
  />
)}
```

Append the `GenerateModal` component to the file:

```tsx
function GenerateModal({
  sessions,
  onClose,
  onGenerated,
  onError,
}: {
  sessions: Session[];
  onClose: () => void;
  onGenerated: (r: Reminder) => void;
  onError: (msg: string) => void;
}) {
  const [sessionId, setSessionId] = useState<number | "">("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (sessionId === "") return;
    setSubmitting(true);
    try {
      const generated = await generateReminderDraft(Number(sessionId));
      onGenerated(generated);
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Generate failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-bg-surface border border-border rounded-lg p-5 w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-text-primary mb-3">Generate reminder draft</h3>
        <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Session</label>
        <select
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value === "" ? "" : Number(e.target.value))}
          className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary mb-4"
        >
          <option value="">Select a scheduled session…</option>
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {new Date(s.start_date).toLocaleDateString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric",
              })}
            </option>
          ))}
        </select>

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={sessionId === "" || submitting}
            className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Generating…" : "Generate"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add the Regenerate button to the DetailPanel**

In the same file, in the `DetailPanel` component, add a handler after the existing handlers:

```tsx
const handleRegenerate = async () => {
  if (!confirm("Replace the current subject and body with a fresh render? Any unsaved edits will be lost.")) {
    return;
  }
  try {
    const updated = await regenerateReminderDraft(reminder.id);
    onChanged(updated);
    onInfo("Reminder regenerated.");
  } catch (e: any) {
    onError(e?.detail ?? e?.message ?? "Regenerate failed");
  }
};
```

In the actions row inside `DetailPanel`, add a Regenerate button between Save draft and Approve, gated on `isDraft`:

```tsx
{isDraft && (
  <>
    <button onClick={handleSave} className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90">
      Save draft
    </button>
    <button onClick={handleRegenerate} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
      Regenerate from template
    </button>
    <button onClick={handleApprove} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
      Approve
    </button>
  </>
)}
```

- [ ] **Step 4: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/reminders/DraftsTab.tsx
git commit -m "feat: add Generate-draft modal and Regenerate button to DraftsTab"
```

---

### Task 9: TemplatesTab — table + create/edit/delete modal

**Files:**
- Modify: `frontend/src/pages/reminders/TemplatesTab.tsx`

- [ ] **Step 1: Replace the placeholder with the full TemplatesTab**

Replace the entire contents of `frontend/src/pages/reminders/TemplatesTab.tsx` with:

```tsx
import { useEffect, useState } from "react";
import {
  createReminderTemplate,
  deleteReminderTemplate,
  fetchReminderTemplates,
  updateReminderTemplate,
  type TemplateInput,
} from "../../api/reminders";
import type { ReminderTemplate, ReminderTemplateType } from "../../types";
import { useToast } from "../../context/ToastContext";

const TYPE_LABEL: Record<ReminderTemplateType, string> = {
  regular_checkin: "Regular check-in",
  activity: "Activity",
};

export function TemplatesTab() {
  const [templates, setTemplates] = useState<ReminderTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<ReminderTemplate | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const { addToast } = useToast();

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchReminderTemplates();
      setTemplates(data);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load templates");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleDelete = async (t: ReminderTemplate) => {
    if (t.is_default) return;
    if (!confirm(`Delete template "${t.name}"?`)) return;
    try {
      await deleteReminderTemplate(t.id);
      addToast("Template deleted.", "success");
      load();
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Delete failed", "error");
    }
  };

  return (
    <div>
      <div className="flex justify-end mb-3">
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90"
        >
          + New template
        </button>
      </div>

      {loading && <p className="text-text-muted text-sm py-4">Loading…</p>}
      {error && <p className="text-error text-sm py-4">{error}</p>}

      {!loading && !error && (
        <div className="border border-border rounded-lg overflow-auto">
          <table className="w-full text-[0.8125rem] border-collapse">
            <thead className="bg-bg-elevated">
              <tr>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Name</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Type</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Lead time</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Default</th>
                <th className="border-b border-border w-32"></th>
              </tr>
            </thead>
            <tbody>
              {templates.map((t) => (
                <tr key={t.id} className="border-b border-border last:border-b-0 hover:bg-bg-elevated/50">
                  <td className="px-3 py-2.5 text-text-primary">{t.name}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{TYPE_LABEL[t.template_type]}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{t.lead_time_days} day{t.lead_time_days === 1 ? "" : "s"}</td>
                  <td className="px-3 py-2.5">
                    {t.is_default && (
                      <span className="inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium bg-accent/[0.12] text-accent">
                        default
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <button
                      onClick={() => setEditing(t)}
                      className="text-text-muted hover:text-accent text-xs mr-2"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleDelete(t)}
                      disabled={t.is_default}
                      className="text-text-muted hover:text-error text-xs disabled:opacity-40 disabled:hover:text-text-muted"
                      title={t.is_default ? "Cannot delete default template" : "Delete"}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {templates.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-text-muted text-sm">
                    No templates yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {(showCreate || editing) && (
        <TemplateModal
          initial={editing}
          onClose={() => {
            setShowCreate(false);
            setEditing(null);
          }}
          onSaved={() => {
            setShowCreate(false);
            setEditing(null);
            load();
          }}
          onError={(msg) => addToast(msg, "error")}
          onInfo={(msg) => addToast(msg, "success")}
        />
      )}
    </div>
  );
}

function TemplateModal({
  initial,
  onClose,
  onSaved,
  onError,
  onInfo,
}: {
  initial: ReminderTemplate | null;
  onClose: () => void;
  onSaved: () => void;
  onError: (msg: string) => void;
  onInfo: (msg: string) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [type, setType] = useState<ReminderTemplateType>(initial?.template_type ?? "regular_checkin");
  const [subject, setSubject] = useState(initial?.subject_template ?? "");
  const [body, setBody] = useState(initial?.body_template ?? "");
  const [leadTime, setLeadTime] = useState(initial?.lead_time_days ?? 3);
  const [isDefault, setIsDefault] = useState(initial?.is_default ?? false);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setSubmitting(true);
    const input: TemplateInput = {
      name,
      template_type: type,
      subject_template: subject,
      body_template: body,
      lead_time_days: leadTime,
      is_default: isDefault,
    };
    try {
      if (initial) {
        await updateReminderTemplate(initial.id, input);
        onInfo("Template updated.");
      } else {
        await createReminderTemplate(input);
        onInfo("Template created.");
      }
      onSaved();
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-bg-surface border border-border rounded-lg p-5 w-full max-w-2xl max-h-[90vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-text-primary mb-3">
          {initial ? "Edit template" : "New template"}
        </h3>

        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Type</label>
            <select
              value={type}
              onChange={(e) => setType(e.target.value as ReminderTemplateType)}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            >
              <option value="regular_checkin">Regular check-in</option>
              <option value="activity">Activity</option>
            </select>
          </div>
        </div>

        <div className="mb-3">
          <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Subject template</label>
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
          />
        </div>

        <div className="mb-3">
          <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Body template</label>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={10}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
          <p className="text-xs text-text-muted mt-1">
            Placeholders: <code>{"{{ date }}"}</code>, <code>{"{{ time }}"}</code>, <code>{"{{ day_of_week }}"}</code>, <code>{"{{ activity_title }}"}</code>, <code>{"{{ activity_instructions }}"}</code>, <code>{"{{ net_control }}"}</code>, <code>{"{{ next_week_preview }}"}</code>
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Lead time (days)</label>
            <input
              type="number"
              min={0}
              value={leadTime}
              onChange={(e) => setLeadTime(Number(e.target.value))}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm text-text-primary">
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
              />
              Default for this type
            </label>
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!name || !subject || !body || submitting}
            className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/reminders/TemplatesTab.tsx
git commit -m "feat: add TemplatesTab with create/edit/delete modal"
```

---

### Task 10: Full verification

- [ ] **Step 1: Run the complete backend test suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass. Count should be at least 8 higher than before this plan started (4 new service tests + 4 new route tests).

- [ ] **Step 2: Verify frontend type-checks**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Manual UI verification (cannot be scripted)**

Sign in as net_control or admin, then in a browser:

**Drafts tab:**
- Navigate to `/reminders` → page renders with Drafts tab active.
- Click "Generate draft" → modal opens with scheduled sessions in the dropdown.
- Select a session → click Generate → modal closes, the new draft is selected in the panel.
- Edit the subject and body → click "Save draft" → confirmation toast.
- Click "Regenerate from template" → confirm dialog → subject/body revert to template-rendered output.
- Click "Approve" → row's status changes to Approved, sub-tab counts update.
- Switch to the Approved sub-tab → row visible.
- Open the row → click "Send" → row transitions to Sent.
- Pick another draft → click "Skip" → row transitions to Skipped.

**Templates tab:**
- Switch to Templates tab → existing default template visible.
- Click "+ New template" → modal opens.
- Fill in a name, type=Regular check-in, subject/body/lead time → click Save → table refreshes.
- Click Edit on the new row → modal opens with current values → change something → Save.
- Click Delete on a non-default template → confirm → row removed.
- Try to Delete the default template → button is disabled with tooltip.

Report any UI issues; do not claim the task complete without performing this verification.
