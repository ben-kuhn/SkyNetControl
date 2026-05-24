# Roster Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `/roster` page so net control can review, edit, preview, approve, send, skip, regenerate roster drafts, and manage roster templates.

**Architecture:** Backend gains one new endpoint (`POST /api/roster/{id}/regenerate`) and one service helper, plus a small one-line permission widen on `DELETE /api/roster/templates/{id}`. Frontend mirrors the Reminders page structure: top tabs (Drafts / Templates), status sub-tabs on Drafts, slide-in detail panel with four stacked section editors plus a Preview modal, Generate-draft modal and Regenerate button. Splitting the page into a shell + two tab files keeps each component focused.

**Tech Stack:** FastAPI, SQLAlchemy, Python, React/TypeScript, Tailwind CSS.

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/api/roster.ts` | API client: list / save / approve / send / skip / generate / regenerate / preview rosters; CRUD templates |
| `frontend/src/pages/RosterPage.tsx` | Top-level page; tab switcher mounting the active tab |
| `frontend/src/pages/roster/DraftsTab.tsx` | Drafts list, sub-tabs, detail panel, Generate modal, Preview modal |
| `frontend/src/pages/roster/TemplatesTab.tsx` | Templates table + form modal |

**Modified files:**

| File | Change |
|------|--------|
| `backend/modules/roster/service.py` | Add `regenerate_draft` |
| `backend/modules/roster/routes.py` | Add `POST /{id}/regenerate`; widen DELETE template role to include NET_CONTROL |
| `tests/test_roster_service.py` | Tests for `regenerate_draft` |
| `tests/test_roster_routes.py` | Tests for the new route |
| `frontend/src/types/index.ts` | Add `Roster`, `RosterStatus`, `RosterTemplate` types |
| `frontend/src/App.tsx` | Replace `PlaceholderPage title="Roster"` with `<RosterPage />` |

---

### Task 1: Backend service — `regenerate_draft`

**Files:**
- Modify: `backend/modules/roster/service.py`
- Test: `tests/test_roster_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_roster_service.py`:

```python
def test_regenerate_roster_draft_rewrites_all_sections(db, season_and_sessions, default_template):
    """regenerate_draft re-renders all five content fields and session_url."""
    from backend.modules.roster.service import regenerate_draft

    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    assert log is not None

    # Hand-edit every section
    log.content_subject = "Edited subject"
    log.content_header = "Edited header"
    log.content_welcome = "Edited welcome"
    log.content_comments = "Edited comments"
    log.content_footer = "Edited footer"
    log.session_url = "https://stale.example/checkins?session=999"
    db.commit()

    result = regenerate_draft(db, log.id)
    assert result is not None
    assert result.id == log.id
    assert result.content_subject != "Edited subject"
    assert result.content_header != "Edited header"
    assert result.content_welcome != "Edited welcome"
    assert result.content_comments != "Edited comments"
    assert result.content_footer != "Edited footer"
    assert result.session_url is not None
    assert "/checkins?session=" in result.session_url


def test_regenerate_roster_draft_picks_up_new_checkins(db, season_and_sessions, default_template):
    """If check-ins are added after generation, regenerate reflects them."""
    from backend.modules.roster.service import regenerate_draft

    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    assert log is not None

    # Add a check-in after the draft was created
    db.add(CheckIn(
        session_id=session1.id,
        callsign="W0NEW",
        name="New Person",
        mode="winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=True,
    ))
    db.commit()

    result = regenerate_draft(db, log.id)
    assert result is not None
    # Expect the regenerated content to mention the new check-in somewhere
    combined = (
        result.content_header + result.content_welcome
        + result.content_comments + result.content_footer
    )
    assert "W0NEW" in combined or "New Person" in combined


def test_regenerate_roster_draft_returns_none_when_not_draft(db, season_and_sessions, default_template):
    """Approved/sent/skipped rosters can't be regenerated."""
    from backend.modules.roster.service import regenerate_draft

    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    approve_roster(db, log.id, "W0NE")

    assert regenerate_draft(db, log.id) is None


def test_regenerate_roster_draft_returns_none_when_missing(db):
    from backend.modules.roster.service import regenerate_draft

    assert regenerate_draft(db, 999) is None
```

If `CheckIn`, `ParseStatus`, `TimingStatus`, `approve_roster` aren't imported at the top of `tests/test_roster_service.py`, add them. Check existing imports first — `CheckIn` and the status enums are likely already imported (the test file builds check-ins in other tests); `approve_roster` is in the service import block.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_roster_service.py::test_regenerate_roster_draft_rewrites_all_sections tests/test_roster_service.py::test_regenerate_roster_draft_picks_up_new_checkins tests/test_roster_service.py::test_regenerate_roster_draft_returns_none_when_not_draft tests/test_roster_service.py::test_regenerate_roster_draft_returns_none_when_missing -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with `ImportError: cannot import name 'regenerate_draft'`.

- [ ] **Step 3: Implement `regenerate_draft`**

Add to `backend/modules/roster/service.py`, after the existing `update_draft` function:

```python
def regenerate_draft(db: Session, roster_id: int) -> RosterLog | None:
    """Re-render a DRAFT roster against the current session, check-ins, and template.

    Returns the updated log, or None if the roster is missing or not in DRAFT status.
    """
    log = db.get(RosterLog, roster_id)
    if log is None or log.status != RosterStatus.DRAFT:
        return None

    net_session = db.get(NetSession, log.session_id)
    if net_session is None:
        return None

    template = None
    if log.template_id is not None:
        template = db.get(RosterTemplate, log.template_id)
    if template is None:
        template = db.query(RosterTemplate).filter(RosterTemplate.is_default.is_(True)).first()
    if template is None:
        return None

    context = build_roster_context(db, net_session)
    sections = render_roster(template, context)
    log.content_subject = sections["subject"]
    log.content_header = sections["header"]
    log.content_welcome = sections["welcome"]
    log.content_comments = sections["comments"]
    log.content_footer = sections["footer"]
    log.session_url = context["session_url"] or None
    db.commit()
    db.refresh(log)
    return log
```

These names should already be in scope at the top of `service.py`: `RosterLog`, `RosterStatus`, `RosterTemplate`, `NetSession`, `build_roster_context`, `render_roster`. Verify with grep before adding.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_roster_service.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All roster service tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/roster/service.py tests/test_roster_service.py
git commit -m "feat: add regenerate_draft service helper for roster"
```

---

### Task 2: Backend route — `POST /api/roster/{id}/regenerate`

**Files:**
- Modify: `backend/modules/roster/routes.py`
- Test: `tests/test_roster_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_roster_routes.py`:

```python
@pytest.mark.anyio
async def test_regenerate_roster_route_rewrites_draft(admin_client, db_setup):
    """Net control / admin can regenerate a draft from the current state."""
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/{'generate'}/{sid}")
    rid = gen_resp.json()["id"]

    # Stale-edit one section to make sure regenerate overwrites it
    with db_setup["factory"]() as session:
        from backend.modules.roster.models import RosterLog
        log = session.get(RosterLog, rid)
        log.content_subject = "Stale subject"
        session.commit()

    resp = await admin_client.post(f"/api/roster/{rid}/regenerate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == rid
    assert data["status"] == "draft"
    assert data["content_subject"] != "Stale subject"


@pytest.mark.anyio
async def test_regenerate_roster_route_404_when_missing(admin_client):
    resp = await admin_client.post("/api/roster/9999/regenerate")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_regenerate_roster_route_409_when_not_draft(admin_client, db_setup):
    """Approved rosters can't be regenerated."""
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/{'generate'}/{sid}")
    rid = gen_resp.json()["id"]
    await admin_client.post(f"/api/roster/{rid}/approve")

    resp = await admin_client.post(f"/api/roster/{rid}/regenerate")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_regenerate_roster_route_requires_role(viewer_client, admin_client, db_setup):
    """Viewer cannot regenerate."""
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/{'generate'}/{sid}")
    rid = gen_resp.json()["id"]

    resp = await viewer_client.post(f"/api/roster/{rid}/regenerate")
    assert resp.status_code == 403
```

Note: roster route tests use `@pytest.mark.anyio`, not `asyncio`. Match that convention. The path construction `/api/roster/{'generate'}/{sid}` is awkward but matches the existing `test_generate_draft_for_session` pattern — feel free to simplify to f-strings if the rest of the file does so.

- [ ] **Step 2: Run tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_roster_routes.py::test_regenerate_roster_route_rewrites_draft tests/test_roster_routes.py::test_regenerate_roster_route_404_when_missing tests/test_roster_routes.py::test_regenerate_roster_route_409_when_not_draft tests/test_roster_routes.py::test_regenerate_roster_route_requires_role -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL — 404 on all because the route doesn't exist yet.

- [ ] **Step 3: Add the route**

In `backend/modules/roster/routes.py`:

Add `regenerate_draft as regenerate_draft_service` to the existing `from backend.modules.roster.service import (...)` block.

Add the route handler near the other action routes (after the skip route around line 281):

```python
@roster_router.post("/{roster_id}/regenerate")
async def regenerate_roster_route(
    roster_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = regenerate_draft_service(db, roster_id)
    if log is None:
        from backend.modules.roster.models import RosterLog
        existing = db.get(RosterLog, roster_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Roster not found")
        raise HTTPException(status_code=409, detail="Roster not in draft status")
    return _roster_to_response(log)
```

If `RosterLog` is already imported at the top of `routes.py`, drop the inline import inside the handler and just reference it directly.

- [ ] **Step 4: Run tests and verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_roster_routes.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/roster/routes.py tests/test_roster_routes.py
git commit -m "feat: add POST /api/roster/{id}/regenerate endpoint"
```

---

### Task 3: Widen DELETE roster template permission

**Files:**
- Modify: `backend/modules/roster/routes.py`

- [ ] **Step 1: Apply the one-line role widen**

In `backend/modules/roster/routes.py`, find the existing DELETE handler:

```python
@roster_router.delete("/templates/{template_id}", status_code=204)
async def delete_template_route(
    template_id: int,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
```

Change the `require_role(UserRole.ADMIN)` to `require_role(UserRole.ADMIN, UserRole.NET_CONTROL)`. All other roster template routes (create / update) already accept both roles; this restores parity and prevents net_control hitting 403 when clicking Delete in the new Templates tab.

- [ ] **Step 2: Run the roster route tests**

Run: `nix-shell --run "python -m pytest tests/test_roster_routes.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass. (No existing test specifically asserts admin-only delete on roster templates, so the role change shouldn't break anything.)

- [ ] **Step 3: Commit**

```bash
git add backend/modules/roster/routes.py
git commit -m "fix: allow net_control to delete roster templates"
```

---

### Task 4: Frontend types

**Files:**
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Append the roster types**

Append to `frontend/src/types/index.ts`:

```typescript
export type RosterStatus = "draft" | "approved" | "sent" | "skipped";

export interface Roster {
  id: number;
  session_id: number;
  template_id: number | null;
  status: RosterStatus;
  content_subject: string;
  content_header: string;
  content_welcome: string;
  content_comments: string;
  content_footer: string;
  session_url: string | null;
  drafted_at: string;
  approved_at: string | null;
  sent_at: string | null;
  approved_by: string | null;
}

export interface RosterTemplate {
  id: number;
  name: string;
  subject_template: string;
  header_template: string;
  welcome_template: string;
  comments_template: string;
  footer_template: string;
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
git commit -m "feat: add Roster and RosterTemplate types"
```

---

### Task 5: Frontend API client

**Files:**
- Create: `frontend/src/api/roster.ts`

- [ ] **Step 1: Create the API client**

Create `frontend/src/api/roster.ts`:

```typescript
import { apiFetch } from "./client";
import type { Roster, RosterTemplate } from "../types";

// --- Rosters ---

export async function fetchRosters(): Promise<Roster[]> {
  return apiFetch<Roster[]>("/roster/");
}

export async function updateRosterDraft(
  id: number,
  body: Partial<Pick<
    Roster,
    "content_subject" | "content_header" | "content_welcome" | "content_comments" | "content_footer"
  >>,
): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function approveRoster(id: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}/approve`, { method: "POST" });
}

export async function sendRoster(id: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}/send`, { method: "POST" });
}

export async function skipRoster(id: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}/skip`, { method: "POST" });
}

export async function regenerateRosterDraft(id: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}/regenerate`, { method: "POST" });
}

export async function generateRosterDraft(sessionId: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/generate/${sessionId}`, { method: "POST" });
}

export async function previewRoster(id: number): Promise<{ text: string }> {
  return apiFetch<{ text: string }>(`/roster/${id}/preview`);
}

// --- Templates ---

export async function fetchRosterTemplates(): Promise<RosterTemplate[]> {
  return apiFetch<RosterTemplate[]>("/roster/templates");
}

export interface RosterTemplateInput {
  name: string;
  subject_template: string;
  header_template: string;
  welcome_template: string;
  comments_template: string;
  footer_template: string;
  lead_time_days: number;
  is_default: boolean;
}

export async function createRosterTemplate(input: RosterTemplateInput): Promise<RosterTemplate> {
  return apiFetch<RosterTemplate>("/roster/templates", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateRosterTemplate(
  id: number,
  input: Partial<RosterTemplateInput>,
): Promise<RosterTemplate> {
  return apiFetch<RosterTemplate>(`/roster/templates/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteRosterTemplate(id: number): Promise<void> {
  await apiFetch<void>(`/roster/templates/${id}`, { method: "DELETE" });
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/roster.ts
git commit -m "feat: add roster API client"
```

---

### Task 6: RosterPage shell + wire route

**Files:**
- Create: `frontend/src/pages/RosterPage.tsx`
- Create: `frontend/src/pages/roster/DraftsTab.tsx` (placeholder)
- Create: `frontend/src/pages/roster/TemplatesTab.tsx` (placeholder)
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create the placeholder tab components**

Create `frontend/src/pages/roster/DraftsTab.tsx`:

```tsx
export function DraftsTab() {
  return <p className="text-text-muted text-sm py-4">Drafts coming next…</p>;
}
```

Create `frontend/src/pages/roster/TemplatesTab.tsx`:

```tsx
export function TemplatesTab() {
  return <p className="text-text-muted text-sm py-4">Templates coming next…</p>;
}
```

- [ ] **Step 2: Create RosterPage with tab switcher**

Create `frontend/src/pages/RosterPage.tsx`:

```tsx
import { useState } from "react";
import { DraftsTab } from "./roster/DraftsTab";
import { TemplatesTab } from "./roster/TemplatesTab";

type TopTab = "drafts" | "templates";

export function RosterPage() {
  const [tab, setTab] = useState<TopTab>("drafts");

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-text-primary mb-4">Roster</h1>

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

- [ ] **Step 3: Wire RosterPage into App.tsx**

In `frontend/src/App.tsx`, add the import near the other page imports:

```tsx
import { RosterPage } from "./pages/RosterPage";
```

Replace the existing `/roster` placeholder route. Find this line in the protected route group:

```tsx
<Route path="/roster" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><PlaceholderPage title="Roster" /></ProtectedRoute>} />
```

Replace with:

```tsx
<Route path="/roster" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><RosterPage /></ProtectedRoute>} />
```

- [ ] **Step 4: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/RosterPage.tsx frontend/src/pages/roster/DraftsTab.tsx frontend/src/pages/roster/TemplatesTab.tsx frontend/src/App.tsx
git commit -m "feat: scaffold RosterPage with tabs and wire route"
```

---

### Task 7: DraftsTab — list with status sub-tabs

**Files:**
- Modify: `frontend/src/pages/roster/DraftsTab.tsx`

- [ ] **Step 1: Replace the placeholder with the list + sub-tabs**

Replace the entire contents of `frontend/src/pages/roster/DraftsTab.tsx` with:

```tsx
import { useEffect, useMemo, useState } from "react";
import { fetchRosters } from "../../api/roster";
import type { Roster, RosterStatus } from "../../types";

const STATUSES: RosterStatus[] = ["draft", "approved", "sent", "skipped"];
const STATUS_LABEL: Record<RosterStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  sent: "Sent",
  skipped: "Skipped",
};
const PILL_CLS: Record<RosterStatus, string> = {
  draft: "bg-warning/[0.12] text-warning",
  approved: "bg-accent/[0.12] text-accent",
  sent: "bg-success/[0.12] text-success",
  skipped: "bg-text-muted/[0.12] text-text-muted",
};

export function DraftsTab() {
  const [rosters, setRosters] = useState<Roster[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<RosterStatus>("draft");

  useEffect(() => {
    fetchRosters()
      .then((data) => {
        setRosters(data);
        setError(null);
      })
      .catch((e) => setError(e?.message ?? "Failed to load rosters"))
      .finally(() => setLoading(false));
  }, []);

  const counts = useMemo(() => {
    const c: Record<RosterStatus, number> = { draft: 0, approved: 0, sent: 0, skipped: 0 };
    for (const r of rosters) c[r.status]++;
    return c;
  }, [rosters]);

  const visible = useMemo(
    () => rosters.filter((r) => r.status === statusFilter),
    [rosters, statusFilter],
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
                  <td className="px-3 py-2.5 text-text-secondary tabular-nums">
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
                    No {STATUS_LABEL[statusFilter].toLowerCase()} rosters.
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

The `Session #{r.session_id}` placeholder gets replaced in Task 8 once we have the sessions data.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/roster/DraftsTab.tsx
git commit -m "feat: add roster DraftsTab list with status sub-tabs and counts"
```

---

### Task 8: DraftsTab — detail panel, session dates, actions, Preview modal

**Files:**
- Modify: `frontend/src/pages/roster/DraftsTab.tsx`

- [ ] **Step 1: Replace the DraftsTab with the full version (detail panel + Preview modal)**

Replace the entire contents of `frontend/src/pages/roster/DraftsTab.tsx` with:

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  approveRoster,
  fetchRosters,
  previewRoster,
  sendRoster,
  skipRoster,
  updateRosterDraft,
} from "../../api/roster";
import { fetchSessions } from "../../api/schedule";
import type { Roster, RosterStatus, Session } from "../../types";
import { useToast } from "../../context/ToastContext";

const STATUSES: RosterStatus[] = ["draft", "approved", "sent", "skipped"];
const STATUS_LABEL: Record<RosterStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  sent: "Sent",
  skipped: "Skipped",
};
const PILL_CLS: Record<RosterStatus, string> = {
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
  const [rosters, setRosters] = useState<Roster[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<RosterStatus>("draft");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [previewText, setPreviewText] = useState<string | null>(null);

  const { addToast } = useToast();

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [rs, ss] = await Promise.all([fetchRosters(), fetchSessions()]);
      setRosters(rs);
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
    const c: Record<RosterStatus, number> = { draft: 0, approved: 0, sent: 0, skipped: 0 };
    for (const r of rosters) c[r.status]++;
    return c;
  }, [rosters]);

  const visible = useMemo(
    () => rosters.filter((r) => r.status === statusFilter),
    [rosters, statusFilter],
  );

  const selected = selectedId ? rosters.find((r) => r.id === selectedId) ?? null : null;

  const handlePreview = async (id: number) => {
    try {
      const { text } = await previewRoster(id);
      setPreviewText(text);
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Preview failed", "error");
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
                        No {STATUS_LABEL[statusFilter].toLowerCase()} rosters.
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
                roster={selected}
                session={sessionById.get(selected.session_id) ?? null}
                onClose={() => setSelectedId(null)}
                onChanged={(updated) =>
                  setRosters((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
                }
                onPreview={() => handlePreview(selected.id)}
                onError={(msg) => addToast(msg, "error")}
                onInfo={(msg) => addToast(msg, "success")}
              />
            </div>
          )}
        </div>
      )}

      {previewText !== null && (
        <PreviewModal text={previewText} onClose={() => setPreviewText(null)} />
      )}
    </div>
  );
}

function DetailPanel({
  roster,
  session,
  onClose,
  onChanged,
  onPreview,
  onError,
  onInfo,
}: {
  roster: Roster;
  session: Session | null;
  onClose: () => void;
  onChanged: (r: Roster) => void;
  onPreview: () => void;
  onError: (msg: string) => void;
  onInfo: (msg: string) => void;
}) {
  const [subject, setSubject] = useState(roster.content_subject);
  const [header, setHeader] = useState(roster.content_header);
  const [welcome, setWelcome] = useState(roster.content_welcome);
  const [comments, setComments] = useState(roster.content_comments);
  const [footer, setFooter] = useState(roster.content_footer);

  useEffect(() => {
    setSubject(roster.content_subject);
    setHeader(roster.content_header);
    setWelcome(roster.content_welcome);
    setComments(roster.content_comments);
    setFooter(roster.content_footer);
  }, [
    roster.id,
    roster.content_subject,
    roster.content_header,
    roster.content_welcome,
    roster.content_comments,
    roster.content_footer,
  ]);

  const isDraft = roster.status === "draft";
  const isApproved = roster.status === "approved";

  const handleSave = async () => {
    try {
      const updated = await updateRosterDraft(roster.id, {
        content_subject: subject,
        content_header: header,
        content_welcome: welcome,
        content_comments: comments,
        content_footer: footer,
      });
      onChanged(updated);
      onInfo("Draft saved.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Save failed");
    }
  };

  const handleApprove = async () => {
    try {
      const updated = await approveRoster(roster.id);
      onChanged(updated);
      onInfo("Roster approved.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Approve failed");
    }
  };

  const handleSend = async () => {
    try {
      const updated = await sendRoster(roster.id);
      onChanged(updated);
      onInfo("Roster sent.");
    } catch (e: any) {
      if (e?.status === 409) {
        onError("Send failed — verify delivery backends are configured (Config page).");
      } else {
        onError(e?.detail ?? e?.message ?? "Send failed");
      }
    }
  };

  const handleSkip = async () => {
    if (!confirm("Skip this roster?")) return;
    try {
      const updated = await skipRoster(roster.id);
      onChanged(updated);
      onInfo("Roster skipped.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Skip failed");
    }
  };

  return (
    <div className="border border-border rounded-lg p-4 bg-bg-surface">
      <div className="flex items-start justify-between mb-3 pb-3 border-b border-border">
        <div>
          <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
            {session ? formatLongDate(session.start_date) : `Session #${roster.session_id}`}
            <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${PILL_CLS[roster.status]}`}>
              {STATUS_LABEL[roster.status]}
            </span>
          </h2>
          <p className="text-xs text-text-muted mt-0.5">
            Drafted {formatLongDate(roster.drafted_at)}
            {roster.approved_at && ` · Approved ${formatLongDate(roster.approved_at)} by ${roster.approved_by}`}
            {roster.sent_at && ` · Sent ${formatLongDate(roster.sent_at)}`}
          </p>
          {roster.session_url && (
            <p className="text-xs mt-1">
              <a href={roster.session_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                {roster.session_url}
              </a>
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

      <SectionInput label="Subject" value={subject} onChange={setSubject} disabled={!isDraft} />
      <SectionTextarea label="Header" value={header} onChange={setHeader} disabled={!isDraft} rows={6} />
      <SectionTextarea label="Welcome" value={welcome} onChange={setWelcome} disabled={!isDraft} rows={6} />
      <SectionTextarea label="Comments" value={comments} onChange={setComments} disabled={!isDraft} rows={6} />
      <SectionTextarea label="Footer" value={footer} onChange={setFooter} disabled={!isDraft} rows={4} />

      <div className="flex gap-2 flex-wrap pt-3 border-t border-border">
        {isDraft && (
          <button onClick={handleSave} className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90">
            Save draft
          </button>
        )}
        <button onClick={onPreview} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
          Preview
        </button>
        {isDraft && (
          <button onClick={handleApprove} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
            Approve
          </button>
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

function SectionInput({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="mb-3">
      <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary disabled:opacity-60"
      />
    </div>
  );
}

function SectionTextarea({
  label,
  value,
  onChange,
  disabled,
  rows,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  rows: number;
}) {
  return (
    <div className="mb-3">
      <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">{label}</label>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        rows={rows}
        className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono disabled:opacity-60"
      />
    </div>
  );
}

function PreviewModal({ text, onClose }: { text: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-bg-surface border border-border rounded-lg p-5 w-full max-w-3xl max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3 pb-3 border-b border-border">
          <h3 className="text-lg font-semibold text-text-primary">Preview</h3>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary p-1 rounded"
            aria-label="Close preview"
          >
            <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <pre className="flex-1 overflow-auto text-[0.8125rem] font-mono text-text-primary whitespace-pre-wrap">
          {text}
        </pre>
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
git add frontend/src/pages/roster/DraftsTab.tsx
git commit -m "feat: add roster DraftsTab detail panel with section editors and Preview modal"
```

---

### Task 9: DraftsTab — Generate-draft modal + Regenerate button

**Files:**
- Modify: `frontend/src/pages/roster/DraftsTab.tsx`

- [ ] **Step 1: Add the imports**

In `frontend/src/pages/roster/DraftsTab.tsx`, update the imports block at the top to include the new functions. The current import is:

```tsx
import {
  approveRoster,
  fetchRosters,
  previewRoster,
  sendRoster,
  skipRoster,
  updateRosterDraft,
} from "../../api/roster";
```

Change to:

```tsx
import {
  approveRoster,
  fetchRosters,
  generateRosterDraft,
  previewRoster,
  regenerateRosterDraft,
  sendRoster,
  skipRoster,
  updateRosterDraft,
} from "../../api/roster";
```

- [ ] **Step 2: Add the Generate-draft button + modal to DraftsTab**

In the `DraftsTab` component, add a state variable next to the other state declarations:

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

At the bottom of the DraftsTab return (just before the final closing `</div>`, after the PreviewModal block), conditionally render the GenerateModal:

```tsx
{showGenerateModal && (
  <GenerateModal
    sessions={sessions.filter((s) => s.status === "completed")}
    onClose={() => setShowGenerateModal(false)}
    onGenerated={(generated) => {
      setRosters((prev) => {
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

Append the `GenerateModal` component at the bottom of the file (after `PreviewModal`):

```tsx
function GenerateModal({
  sessions,
  onClose,
  onGenerated,
  onError,
}: {
  sessions: Session[];
  onClose: () => void;
  onGenerated: (r: Roster) => void;
  onError: (msg: string) => void;
}) {
  const [sessionId, setSessionId] = useState<number | "">("");
  const [submitting, setSubmitting] = useState(false);

  const ordered = useMemo(
    () => [...sessions].sort((a, b) => b.start_date.localeCompare(a.start_date)),
    [sessions],
  );

  const handleSubmit = async () => {
    if (sessionId === "") return;
    setSubmitting(true);
    try {
      const generated = await generateRosterDraft(Number(sessionId));
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
        <h3 className="text-lg font-semibold text-text-primary mb-3">Generate roster draft</h3>
        <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Session</label>
        <select
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value === "" ? "" : Number(e.target.value))}
          className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary mb-4"
        >
          <option value="">Select a completed session…</option>
          {ordered.map((s) => (
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

`useMemo` is already imported at the top of the file.

- [ ] **Step 3: Add the Regenerate button to the DetailPanel**

In `DetailPanel`, add a handler after `handleSkip`:

```tsx
const handleRegenerate = async () => {
  if (!confirm("Replace all sections with a fresh render from current check-ins? Any unsaved edits will be lost.")) {
    return;
  }
  try {
    const updated = await regenerateRosterDraft(roster.id);
    onChanged(updated);
    onInfo("Roster regenerated.");
  } catch (e: any) {
    onError(e?.detail ?? e?.message ?? "Regenerate failed");
  }
};
```

In the actions row, insert a Regenerate button between Approve and Skip. The current draft actions look like:

```tsx
{isDraft && (
  <button onClick={handleSave} className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90">
    Save draft
  </button>
)}
<button onClick={onPreview} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
  Preview
</button>
{isDraft && (
  <button onClick={handleApprove} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
    Approve
  </button>
)}
```

Change to:

```tsx
{isDraft && (
  <button onClick={handleSave} className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90">
    Save draft
  </button>
)}
<button onClick={onPreview} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
  Preview
</button>
{isDraft && (
  <button onClick={handleRegenerate} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
    Regenerate from check-ins
  </button>
)}
{isDraft && (
  <button onClick={handleApprove} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
    Approve
  </button>
)}
```

- [ ] **Step 4: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/roster/DraftsTab.tsx
git commit -m "feat: add Generate-draft modal and Regenerate button to roster DraftsTab"
```

---

### Task 10: TemplatesTab — table + create/edit/delete modal

**Files:**
- Modify: `frontend/src/pages/roster/TemplatesTab.tsx`

- [ ] **Step 1: Replace the placeholder with the full TemplatesTab**

Replace the entire contents of `frontend/src/pages/roster/TemplatesTab.tsx` with:

```tsx
import { useEffect, useState } from "react";
import {
  createRosterTemplate,
  deleteRosterTemplate,
  fetchRosterTemplates,
  updateRosterTemplate,
  type RosterTemplateInput,
} from "../../api/roster";
import type { RosterTemplate } from "../../types";
import { useToast } from "../../context/ToastContext";

const PLACEHOLDER_HINT =
  "Placeholders: {{ date }}, {{ time }}, {{ day_of_week }}, {{ net_control }}, {{ activity_title }}, {{ activity_instructions }}, {{ next_week_preview }}, {{ session_url }}, {{ total_count }}, {{ checkins }} (use {% for c in checkins %}…{% endfor %}), {{ new_members }}";

export function TemplatesTab() {
  const [templates, setTemplates] = useState<RosterTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<RosterTemplate | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const { addToast } = useToast();

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchRosterTemplates();
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

  const handleDelete = async (t: RosterTemplate) => {
    if (t.is_default) return;
    if (!confirm(`Delete template "${t.name}"?`)) return;
    try {
      await deleteRosterTemplate(t.id);
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
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Lead time</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Default</th>
                <th className="border-b border-border w-32"></th>
              </tr>
            </thead>
            <tbody>
              {templates.map((t) => (
                <tr key={t.id} className="border-b border-border last:border-b-0 hover:bg-bg-elevated/50">
                  <td className="px-3 py-2.5 text-text-primary">{t.name}</td>
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
                  <td colSpan={4} className="px-3 py-8 text-center text-text-muted text-sm">
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
  initial: RosterTemplate | null;
  onClose: () => void;
  onSaved: () => void;
  onError: (msg: string) => void;
  onInfo: (msg: string) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [subject, setSubject] = useState(initial?.subject_template ?? "");
  const [header, setHeader] = useState(initial?.header_template ?? "");
  const [welcome, setWelcome] = useState(initial?.welcome_template ?? "");
  const [comments, setComments] = useState(initial?.comments_template ?? "");
  const [footer, setFooter] = useState(initial?.footer_template ?? "");
  const [leadTime, setLeadTime] = useState(initial?.lead_time_days ?? 1);
  const [isDefault, setIsDefault] = useState(initial?.is_default ?? false);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setSubmitting(true);
    const input: RosterTemplateInput = {
      name,
      subject_template: subject,
      header_template: header,
      welcome_template: welcome,
      comments_template: comments,
      footer_template: footer,
      lead_time_days: leadTime,
      is_default: isDefault,
    };
    try {
      if (initial) {
        await updateRosterTemplate(initial.id, input);
        onInfo("Template updated.");
      } else {
        await createRosterTemplate(input);
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
        className="bg-bg-surface border border-border rounded-lg p-5 w-full max-w-3xl max-h-[90vh] overflow-auto"
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
            <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Lead time (days)</label>
            <input
              type="number"
              min={0}
              value={leadTime}
              onChange={(e) => setLeadTime(Number(e.target.value))}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
        </div>

        <Field label="Subject template">
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
          />
        </Field>

        <Field label="Header template">
          <textarea
            value={header}
            onChange={(e) => setHeader(e.target.value)}
            rows={4}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
        </Field>

        <Field label="Welcome template">
          <textarea
            value={welcome}
            onChange={(e) => setWelcome(e.target.value)}
            rows={4}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
        </Field>

        <Field label="Comments template">
          <textarea
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            rows={4}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
        </Field>

        <Field label="Footer template">
          <textarea
            value={footer}
            onChange={(e) => setFooter(e.target.value)}
            rows={3}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
        </Field>

        <p className="text-xs text-text-muted mb-3">{PLACEHOLDER_HINT}</p>

        <div className="mb-4">
          <label className="flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            Default template
          </label>
        </div>

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!name || !subject || !header || !welcome || !comments || !footer || submitting}
            className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">{label}</label>
      {children}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/roster/TemplatesTab.tsx
git commit -m "feat: add roster TemplatesTab with create/edit/delete modal"
```

---

### Task 11: Full verification

- [ ] **Step 1: Run the complete backend test suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass. Count should be at least 8 higher than the pre-plan baseline (4 new service tests + 4 new route tests).

- [ ] **Step 2: Verify frontend type-checks**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Manual UI verification (cannot be scripted)**

Sign in as net_control or admin, then in a browser:

**Drafts tab:**
- Navigate to `/roster` → page renders with Drafts tab active.
- Click "+ Generate draft" → modal opens with completed sessions in descending date order.
- Select a session → click Generate → modal closes, the new draft is selected.
- Edit each of the four section textareas → click "Save draft" → toast confirms.
- Click "Preview" → modal renders the assembled body. Close.
- Click "Regenerate from check-ins" → confirm dialog → sections revert to template-rendered output.
- Click "Approve" → status transitions to Approved, sub-tab counts update.
- Switch to Approved sub-tab → click the row → click "Send" → status transitions to Sent (requires delivery backends configured).
- Pick another draft → click "Skip" → transitions to Skipped.

**Templates tab:**
- Switch to Templates tab → existing default template visible.
- Click "+ New template" → modal opens with empty fields and placeholder hint.
- Fill in all fields → click Save → table refreshes.
- Click Edit on the new row → modal pre-filled → change something → Save.
- Click Delete on a non-default template → confirm → row removed.
- Try Delete on the default template → button is disabled with tooltip.

Report any UI issues; do not claim the task complete without performing this verification.
