# Members Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `/members` page — a sortable, searchable table of long-term members with a slide-in detail panel that shows each member's full check-in history.

**Architecture:** One new backend endpoint (`GET /api/checkins/by-callsign/{callsign}`) and one new frontend page (`MembersPage`) plus its API-client and nav wiring. The page uses the same two-pane layout pattern as `CheckInsPage`. No pagination — client-side filter and sort over the full member list.

**Tech Stack:** FastAPI, SQLAlchemy, React/TypeScript, Tailwind CSS, React Router.

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/api/members.ts` | API client: `fetchMembers`, `fetchMemberHistory` |
| `frontend/src/pages/MembersPage.tsx` | The page itself — table, search, sort, detail panel |

**Modified files:**

| File | Change |
|------|--------|
| `backend/modules/checkins/routes.py` | Add `GET /by-callsign/{callsign}` route + a `_checkin_to_response_with_session` helper that includes `session_date` |
| `backend/modules/checkins/service.py` | Add `get_checkins_by_callsign(db, callsign)` helper |
| `tests/test_checkin_routes.py` | Tests for the new endpoint |
| `frontend/src/types/index.ts` | Add `Member` and `MemberCheckin` types |
| `frontend/src/App.tsx` | Register `/members` route |
| `frontend/src/layouts/Sidebar.tsx` | Add "Members" nav entry |

---

### Task 1: Backend service helper

**Files:**
- Modify: `backend/modules/checkins/service.py`
- Test: `tests/test_checkin_service.py` (new test added)

- [ ] **Step 1: Write the failing test**

`tests/test_checkin_service.py` already has a `db` fixture (lines 33-34). Add at the bottom:

```python
def test_get_checkins_by_callsign_returns_all_sessions_desc(db):
    """Returns (CheckIn, session_date) tuples for a callsign across sessions, newest first."""
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
    from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
    from backend.modules.checkins.service import get_checkins_by_callsign

    season = NetSeason(name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                       day_of_week=3, time=time(18, 0))
    db.add(season); db.flush()
    s1 = NetSession(season_id=season.id, start_date=date(2026, 1, 15),
                    session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.COMPLETED)
    s2 = NetSession(season_id=season.id, start_date=date(2026, 2, 15),
                    session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.COMPLETED)
    db.add_all([s1, s2]); db.commit()

    db.add_all([
        CheckIn(session_id=s1.id, callsign="W0NE", name="A", mode="Voice",
                parse_status=ParseStatus.AUTO, timing_status=TimingStatus.ON_TIME, is_new_member=True),
        CheckIn(session_id=s2.id, callsign="W0NE", name="A", mode="Winlink",
                parse_status=ParseStatus.AUTO, timing_status=TimingStatus.ON_TIME, is_new_member=False),
        CheckIn(session_id=s1.id, callsign="K0XYZ", name="B", mode="Voice",
                parse_status=ParseStatus.AUTO, timing_status=TimingStatus.ON_TIME, is_new_member=True),
    ])
    db.commit()

    rows = get_checkins_by_callsign(db, "W0NE")
    # Newest first
    assert [r[0].mode for r in rows] == ["Winlink", "Voice"]
    assert [r[1] for r in rows] == [date(2026, 2, 15), date(2026, 1, 15)]

    # Case-insensitive
    rows_lower = get_checkins_by_callsign(db, "w0ne")
    assert [r[0].id for r in rows_lower] == [r[0].id for r in rows]

    assert get_checkins_by_callsign(db, "NOBODY") == []
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `nix-shell --run "python -m pytest tests/test_checkin_service.py::test_get_checkins_by_callsign_returns_all_sessions_desc -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with `ImportError: cannot import name 'get_checkins_by_callsign'`.

- [ ] **Step 3: Implement the helper**

Add to `backend/modules/checkins/service.py`:

```python
def get_checkins_by_callsign(db: Session, callsign: str) -> list[tuple[CheckIn, date]]:
    """All check-ins for a callsign with their session date, newest first."""
    from backend.modules.schedule.models import NetSession

    normalized = callsign.upper()
    return (
        db.query(CheckIn, NetSession.start_date)
        .join(NetSession, CheckIn.session_id == NetSession.id)
        .filter(CheckIn.callsign == normalized)
        .order_by(NetSession.start_date.desc(), CheckIn.id.desc())
        .all()
    )
```

If `date` is not already imported from `datetime` at the top of `service.py`, add it to the existing datetime import.

Before adding, check the top of `backend/modules/checkins/service.py` and confirm `CheckIn` is already imported. If it isn't, add `from backend.modules.checkins.models import CheckIn` to the existing imports.

- [ ] **Step 4: Run the test and verify it passes**

Run: `nix-shell --run "python -m pytest tests/test_checkin_service.py::test_get_checkins_by_callsign_returns_all_sessions_desc -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/checkins/service.py tests/test_checkin_service.py
git commit -m "feat: add get_checkins_by_callsign service helper"
```

---

### Task 2: Backend route

**Files:**
- Modify: `backend/modules/checkins/routes.py`
- Test: `tests/test_checkin_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_checkin_routes.py` (after the existing tests):

```python
@pytest.mark.asyncio
async def test_get_checkins_by_callsign_returns_history(test_client, test_settings, db_setup):
    """Returns the callsign's check-ins across sessions with embedded session_date."""
    from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus

    with db_setup() as session:
        # db_setup already provides at least one NetSession; create a check-in on it.
        net_session = session.query(NetSession).first()
        checkin = CheckIn(
            session_id=net_session.id,
            callsign="W0NE",
            name="Test",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
            is_new_member=False,
        )
        session.add(checkin)
        session.commit()

    viewer_token = create_access_token("KD0TST", "viewer", test_settings)
    resp = await test_client.get(
        "/api/checkins/by-callsign/w0ne",  # case-insensitive
        cookies={"access_token": viewer_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["callsign"] == "W0NE"
    assert body[0]["mode"] == "Winlink"
    assert "session_date" in body[0]  # embedded for frontend convenience


@pytest.mark.asyncio
async def test_get_checkins_by_callsign_empty(test_client, test_settings):
    """Unknown callsign returns 200 with empty list, not 404."""
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)
    resp = await test_client.get(
        "/api/checkins/by-callsign/NOBODY",
        cookies={"access_token": viewer_token},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_checkins_by_callsign_requires_auth(test_client):
    """Endpoint requires authentication."""
    resp = await test_client.get("/api/checkins/by-callsign/W0NE")
    assert resp.status_code == 401
```

The existing test file already imports `pytest`, `create_access_token`, `NetSession`, and uses fixtures `test_client`, `test_settings`, and `db_setup`. Reuse them — no new imports needed beyond `CheckIn`, `ParseStatus`, `TimingStatus`, which the test imports inline.

- [ ] **Step 2: Run tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_checkin_routes.py::test_get_checkins_by_callsign_returns_history tests/test_checkin_routes.py::test_get_checkins_by_callsign_empty tests/test_checkin_routes.py::test_get_checkins_by_callsign_requires_auth -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL — 404 on first two (route doesn't exist), 404 on third (no route, so no auth gate either).

- [ ] **Step 3: Add the route**

In `backend/modules/checkins/routes.py`:

Add `get_checkins_by_callsign` to the existing service import block at line 14 (`from backend.modules.checkins.service import (...)`).

Add this helper near the existing `_checkin_to_response` function (around line 35):

```python
def _checkin_to_response_with_session(checkin: CheckIn, session_date) -> dict:
    base = _checkin_to_response(checkin)
    base["session_date"] = session_date.isoformat()
    return base
```

Add the route after the existing `/members` route (around line 198):

```python
@checkins_router.get("/by-callsign/{callsign}")
async def get_checkins_by_callsign_route(
    callsign: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    rows = get_checkins_by_callsign(db, callsign)
    return [_checkin_to_response_with_session(c, d) for c, d in rows]
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_checkin_routes.py::test_get_checkins_by_callsign_returns_history tests/test_checkin_routes.py::test_get_checkins_by_callsign_empty tests/test_checkin_routes.py::test_get_checkins_by_callsign_requires_auth -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/checkins/routes.py tests/test_checkin_routes.py
git commit -m "feat: add GET /api/checkins/by-callsign/{callsign} endpoint"
```

---

### Task 3: Frontend types

**Files:**
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Add Member and MemberCheckin types**

Append to `frontend/src/types/index.ts`:

```typescript
export interface Member {
  callsign: string;
  name: string;
  first_check_in_date: string;
  last_check_in_date: string;
  total_check_ins: number;
}

export interface MemberCheckin extends CheckIn {
  session_date: string;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat: add Member and MemberCheckin types"
```

---

### Task 4: Frontend API client

**Files:**
- Create: `frontend/src/api/members.ts`

- [ ] **Step 1: Create the API client**

Create `frontend/src/api/members.ts`:

```typescript
import { apiFetch } from "./client";
import type { Member, MemberCheckin } from "../types";

export async function fetchMembers(): Promise<Member[]> {
  return apiFetch<Member[]>("/checkins/members");
}

export async function fetchMemberHistory(callsign: string): Promise<MemberCheckin[]> {
  return apiFetch<MemberCheckin[]>(`/checkins/by-callsign/${encodeURIComponent(callsign)}`);
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/members.ts
git commit -m "feat: add members API client"
```

---

### Task 5: MembersPage — table with search and sort

**Files:**
- Create: `frontend/src/pages/MembersPage.tsx`

- [ ] **Step 1: Create the page with table, search, and sort (no drill-down yet)**

Create `frontend/src/pages/MembersPage.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import { fetchMembers } from "../api/members";
import type { Member } from "../types";

type SortKey = "callsign" | "name" | "first_check_in_date" | "last_check_in_date" | "total_check_ins";
type SortDir = "asc" | "desc";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function MembersPage() {
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("callsign");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  useEffect(() => {
    fetchMembers()
      .then((data) => {
        setMembers(data);
        setError(null);
      })
      .catch((e) => setError(e?.message ?? "Failed to load members"))
      .finally(() => setLoading(false));
  }, []);

  const filteredSorted = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q
      ? members.filter(
          (m) => m.callsign.toLowerCase().includes(q) || m.name.toLowerCase().includes(q),
        )
      : members;
    const sorted = [...filtered].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [members, search, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const sortIndicator = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-text-primary mb-4">Members</h1>

      <div className="mb-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search callsign or name…"
          className="w-full max-w-sm px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent"
        />
      </div>

      {loading && <p className="text-text-muted text-sm py-4">Loading…</p>}
      {error && <p className="text-error text-sm py-4">{error}</p>}

      {!loading && !error && (
        <div className="border border-border rounded-lg overflow-auto">
          <table className="w-full text-[0.8125rem] border-collapse">
            <thead className="bg-bg-elevated">
              <tr>
                {([
                  ["callsign", "Callsign"],
                  ["name", "Name"],
                  ["first_check_in_date", "First check-in"],
                  ["last_check_in_date", "Last check-in"],
                  ["total_check_ins", "Total"],
                ] as [SortKey, string][]).map(([key, label]) => (
                  <th
                    key={key}
                    onClick={() => toggleSort(key)}
                    className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border cursor-pointer select-none hover:text-text-primary"
                  >
                    {label}{sortIndicator(key)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredSorted.map((m) => (
                <tr
                  key={m.callsign}
                  className="border-b border-border last:border-b-0 hover:bg-bg-elevated/50"
                >
                  <td className="px-3 py-2.5 font-mono font-semibold text-text-primary">{m.callsign}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{m.name}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{formatDate(m.first_check_in_date)}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{formatDate(m.last_check_in_date)}</td>
                  <td className="px-3 py-2.5 text-right text-text-secondary">{m.total_check_ins}</td>
                </tr>
              ))}
              {filteredSorted.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-text-muted text-sm">
                    {members.length === 0
                      ? "No members yet. Members are added automatically when their check-ins are approved."
                      : "No members match your search."}
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

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/MembersPage.tsx
git commit -m "feat: add MembersPage table with search and sort"
```

---

### Task 6: Wire route and sidebar entry

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/layouts/Sidebar.tsx`

- [ ] **Step 1: Register the /members route in App.tsx**

In `frontend/src/App.tsx`, add this import after the other page imports (after the `CheckInsPage` import on line 14):

```tsx
import { MembersPage } from "./pages/MembersPage";
```

Inside the inner `<Routes>`, immediately after the `/checkins` route (currently line 60), add:

```tsx
<Route path="/members" element={<ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}><MembersPage /></ProtectedRoute>} />
```

- [ ] **Step 2: Add the sidebar nav entry**

In `frontend/src/layouts/Sidebar.tsx`, modify the `navItems` array to insert a "Members" entry directly after "Check-ins":

```tsx
const navItems: NavItem[] = [
  { label: "Schedule", to: "/schedule", minRole: ["pending", "viewer", "net_control", "admin"] },
  { label: "Check-ins", to: "/checkins", minRole: ["viewer", "net_control", "admin"] },
  { label: "Members", to: "/members", minRole: ["viewer", "net_control", "admin"] },
  { label: "Reminders", to: "/reminders", minRole: ["net_control", "admin"] },
  { label: "Roster", to: "/roster", minRole: ["net_control", "admin"] },
  { label: "Activities", to: "/activities", minRole: ["admin"] },
  { label: "Users", to: "/users", minRole: ["admin"] },
  { label: "Config", to: "/config", minRole: ["admin"] },
];
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx frontend/src/layouts/Sidebar.tsx
git commit -m "feat: register /members route and sidebar entry"
```

---

### Task 7: MembersPage — drill-down detail panel

**Files:**
- Modify: `frontend/src/pages/MembersPage.tsx`

- [ ] **Step 1: Extract the table into a `MembersTable` sub-component and add the detail panel**

Replace the entire contents of `frontend/src/pages/MembersPage.tsx` with:

```tsx
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchMembers, fetchMemberHistory } from "../api/members";
import type { Member, MemberCheckin } from "../types";

type SortKey = "callsign" | "name" | "first_check_in_date" | "last_check_in_date" | "total_check_ins";
type SortDir = "asc" | "desc";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function MembersPage() {
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("callsign");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [selectedCallsign, setSelectedCallsign] = useState<string | null>(null);

  useEffect(() => {
    fetchMembers()
      .then((data) => {
        setMembers(data);
        setError(null);
      })
      .catch((e) => setError(e?.message ?? "Failed to load members"))
      .finally(() => setLoading(false));
  }, []);

  const filteredSorted = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q
      ? members.filter(
          (m) => m.callsign.toLowerCase().includes(q) || m.name.toLowerCase().includes(q),
        )
      : members;
    const sorted = [...filtered].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [members, search, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const sortIndicator = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  const selectedMember = selectedCallsign
    ? members.find((m) => m.callsign === selectedCallsign) ?? null
    : null;

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-text-primary mb-4">Members</h1>

      <div className="mb-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search callsign or name…"
          className="w-full max-w-sm px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent"
        />
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
                    {([
                      ["callsign", "Callsign"],
                      ["name", "Name"],
                      ["first_check_in_date", "First check-in"],
                      ["last_check_in_date", "Last check-in"],
                      ["total_check_ins", "Total"],
                    ] as [SortKey, string][]).map(([key, label]) => (
                      <th
                        key={key}
                        onClick={() => toggleSort(key)}
                        className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border cursor-pointer select-none hover:text-text-primary"
                      >
                        {label}{sortIndicator(key)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredSorted.map((m) => {
                    const isSelected = m.callsign === selectedCallsign;
                    return (
                      <tr
                        key={m.callsign}
                        onClick={() => setSelectedCallsign(isSelected ? null : m.callsign)}
                        className={`border-b border-border last:border-b-0 cursor-pointer transition-colors ${
                          isSelected
                            ? "bg-accent/[0.08] border-l-2 border-l-accent"
                            : "hover:bg-bg-elevated/50"
                        }`}
                      >
                        <td className="px-3 py-2.5 font-mono font-semibold text-text-primary">{m.callsign}</td>
                        <td className="px-3 py-2.5 text-text-secondary">{m.name}</td>
                        <td className="px-3 py-2.5 text-text-secondary">{formatDate(m.first_check_in_date)}</td>
                        <td className="px-3 py-2.5 text-text-secondary">{formatDate(m.last_check_in_date)}</td>
                        <td className="px-3 py-2.5 text-right text-text-secondary">{m.total_check_ins}</td>
                      </tr>
                    );
                  })}
                  {filteredSorted.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-3 py-8 text-center text-text-muted text-sm">
                        {members.length === 0
                          ? "No members yet. Members are added automatically when their check-ins are approved."
                          : "No members match your search."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {selectedMember && (
            <div className="flex-1 min-w-0">
              <MemberDetailPanel
                member={selectedMember}
                onClose={() => setSelectedCallsign(null)}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MemberDetailPanel({ member, onClose }: { member: Member; onClose: () => void }) {
  const navigate = useNavigate();
  const [history, setHistory] = useState<MemberCheckin[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);

  useEffect(() => {
    setHistoryLoading(true);
    setHistoryError(null);
    setHistory(null);
    fetchMemberHistory(member.callsign)
      .then(setHistory)
      .catch((e) => setHistoryError(e?.message ?? "Failed to load history"))
      .finally(() => setHistoryLoading(false));
  }, [member.callsign]);

  return (
    <div className="border border-border rounded-lg p-4 bg-bg-surface">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h2 className="text-lg font-mono font-semibold text-text-primary">{member.callsign}</h2>
          <p className="text-sm text-text-secondary">{member.name}</p>
        </div>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary p-1 rounded"
          aria-label="Close detail panel"
        >
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="grid grid-cols-3 gap-3 text-xs mb-4 border-b border-border pb-3">
        <div>
          <div className="text-text-muted uppercase tracking-wider">First</div>
          <div className="text-text-primary">{formatDate(member.first_check_in_date)}</div>
        </div>
        <div>
          <div className="text-text-muted uppercase tracking-wider">Last</div>
          <div className="text-text-primary">{formatDate(member.last_check_in_date)}</div>
        </div>
        <div>
          <div className="text-text-muted uppercase tracking-wider">Total</div>
          <div className="text-text-primary">{member.total_check_ins}</div>
        </div>
      </div>

      <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">History</h3>

      {historyLoading && <p className="text-text-muted text-sm">Loading…</p>}
      {historyError && <p className="text-error text-sm">{historyError}</p>}
      {history && history.length === 0 && (
        <p className="text-text-muted text-sm italic">No check-ins recorded for this callsign.</p>
      )}
      {history && history.length > 0 && (
        <ul className="space-y-1.5">
          {history.map((c) => (
            <li
              key={c.id}
              onClick={() => navigate(`/checkins?session=${c.session_id}`)}
              className="px-2.5 py-1.5 rounded cursor-pointer hover:bg-bg-elevated/50 text-sm flex items-baseline gap-3"
              title={c.comments ?? ""}
            >
              <span className="text-text-primary font-medium w-28">{formatDate(c.session_date)}</span>
              <span className="text-text-secondary">{c.mode}</span>
              {c.comments && (
                <span className="text-text-muted text-xs italic truncate">{c.comments}</span>
              )}
            </li>
          ))}
        </ul>
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
git add frontend/src/pages/MembersPage.tsx
git commit -m "feat: add drill-down detail panel to MembersPage"
```

---

### Task 8: Full verification

- [ ] **Step 1: Run the complete backend test suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass; test count should be at least 3 higher than before (one for the service helper, three for the route).

- [ ] **Step 2: Verify the frontend type-checks**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Manual verification (cannot be scripted)**

Start the dev server and click around in a browser:
- Visit `/members` — table renders with sorted member list, search input is focused-able.
- Type in search — table filters by callsign or name (case-insensitive).
- Click each column header — table sorts asc/desc, indicator arrow flips.
- Click a row — detail panel slides in on the right (stacks below on narrow viewports).
- Detail panel shows callsign, name, stats, history list.
- Click a history row — navigates to `/checkins?session=<id>` and that session is selected.
- Click the close button on the panel — panel closes, no row highlighted.
- Click the same row twice — second click deselects.

Report any UI issues; do not claim the task complete without performing this verification.
