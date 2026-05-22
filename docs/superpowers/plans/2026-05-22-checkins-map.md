# Check-ins Map Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Leaflet map pane to the Check-ins page with interactive pins and bidirectional selection between table rows and map markers.

**Architecture:** A new `CheckInMap` component renders a Leaflet map inside the existing CheckInsPage using a responsive two-pane layout. Check-in data (already loaded) drives pin placement — no new API calls. The standalone `/map` route and nav entry are removed.

**Tech Stack:** Leaflet 1.9 (already installed), React/TypeScript, Tailwind CSS

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/components/CheckInMap.tsx` | Leaflet map component with CircleMarker pins and selection |

**Modified files:**

| File | Change |
|------|--------|
| `frontend/src/pages/CheckInsPage.tsx` | Two-pane layout, `selectedCheckinId` state, row click/highlight, integrate CheckInMap |
| `frontend/src/App.tsx` | Remove `/map` route |
| `frontend/src/layouts/Sidebar.tsx` | Remove Map nav entry |

---

### Task 1: CheckInMap Component

**Files:**
- Create: `frontend/src/components/CheckInMap.tsx`

- [x] **Step 1: Create the CheckInMap component**

Create `frontend/src/components/CheckInMap.tsx`:

```tsx
import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { CheckIn } from "../types";

const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/">CARTO</a>';

const DEFAULT_CENTER: L.LatLngExpression = [39.8283, -98.5795]; // US center
const DEFAULT_ZOOM = 4;

const ACCENT = "#22d3ee";
const WARNING = "#fbbf24";

interface Props {
  checkins: CheckIn[];
  selectedCheckinId: number | null;
  onSelectCheckin: (id: number) => void;
}

export function CheckInMap({ checkins, selectedCheckinId, onSelectCheckin }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<Map<number, L.CircleMarker>>(new Map());

  // Initialize map once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      zoomControl: true,
      attributionControl: true,
    });

    L.tileLayer(TILE_URL, { attribution: TILE_ATTR, maxZoom: 18 }).addTo(map);
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Render markers when checkins change
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // Clear existing markers
    markersRef.current.forEach((m) => m.remove());
    markersRef.current.clear();

    const withCoords = checkins.filter(
      (c): c is CheckIn & { latitude: number; longitude: number } =>
        c.latitude != null && c.longitude != null,
    );

    if (withCoords.length === 0) {
      map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
      return;
    }

    for (const c of withCoords) {
      const isSelected = c.id === selectedCheckinId;
      const marker = L.circleMarker([c.latitude, c.longitude], {
        radius: isSelected ? 10 : 6,
        fillColor: c.is_new_member ? WARNING : ACCENT,
        fillOpacity: isSelected ? 1 : 0.6,
        color: isSelected ? "#ffffff" : "transparent",
        weight: isSelected ? 2 : 0,
      });

      marker.bindPopup(
        `<strong style="font-family:monospace">${c.callsign}</strong><br/>${c.name}`,
        { closeButton: false, className: "checkin-popup" },
      );

      marker.on("click", () => onSelectCheckin(c.id));
      marker.addTo(map);
      markersRef.current.set(c.id, marker);
    }

    // Fit bounds to all markers
    const bounds = L.latLngBounds(withCoords.map((c) => [c.latitude, c.longitude]));
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
  }, [checkins, selectedCheckinId, onSelectCheckin]);

  // Pan to selected marker and open popup
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selectedCheckinId) return;

    const marker = markersRef.current.get(selectedCheckinId);
    if (marker) {
      map.panTo(marker.getLatLng(), { animate: true });
      marker.openPopup();
    }
  }, [selectedCheckinId]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full min-h-[400px] rounded-lg border border-border overflow-hidden"
    />
  );
}
```

- [x] **Step 2: Verify build compiles**

Run: `cd frontend && nix-shell --run "npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors

- [x] **Step 3: Commit**

```bash
git add frontend/src/components/CheckInMap.tsx
git commit -m "feat: add CheckInMap Leaflet component with pin rendering and selection"
```

---

### Task 2: Integrate Map into CheckInsPage

**Files:**
- Modify: `frontend/src/pages/CheckInsPage.tsx`

- [x] **Step 1: Add selectedCheckinId state and row selection to CheckinTable**

In `frontend/src/pages/CheckInsPage.tsx`, update the `CheckinTable` component signature and add row click handling. Add `useRef` to the existing React import on line 1.

Replace the `CheckinTable` function (lines 105–185) with:

```tsx
function CheckinTable({
  checkins,
  canEditCheckins,
  selectedCheckinId,
  onSelectCheckin,
  onEdit,
}: {
  checkins: CheckIn[];
  canEditCheckins: boolean;
  selectedCheckinId: number | null;
  onSelectCheckin: (id: number | null) => void;
  onEdit: (c: CheckIn) => void;
}) {
  const rowRefs = useRef<Map<number, HTMLTableRowElement>>(new Map());

  // Scroll selected row into view (when triggered by map pin click)
  useEffect(() => {
    if (selectedCheckinId) {
      const row = rowRefs.current.get(selectedCheckinId);
      if (row) {
        row.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }
  }, [selectedCheckinId]);

  return (
    <div className="border border-border rounded-lg overflow-auto">
      <table className="w-full text-[0.8125rem] border-collapse">
        <thead className="bg-bg-elevated">
          <tr>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Callsign</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Name</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Location</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Mode</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Status</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Timing</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">New</th>
            {canEditCheckins && <th className="border-b border-border w-10"></th>}
          </tr>
        </thead>
        <tbody>
          {checkins.map((c) => {
            const isSelected = c.id === selectedCheckinId;
            return (
              <React.Fragment key={c.id}>
              <tr
                ref={(el) => { if (el) rowRefs.current.set(c.id, el); else rowRefs.current.delete(c.id); }}
                onClick={() => onSelectCheckin(isSelected ? null : c.id)}
                className={`${c.comments ? "" : "border-b border-border last:border-b-0"} cursor-pointer transition-colors ${
                  isSelected
                    ? "bg-accent/[0.08] border-l-2 border-l-accent"
                    : c.parse_status === "manual_review"
                      ? "bg-warning/[0.04] hover:bg-bg-elevated/50"
                      : "hover:bg-bg-elevated/50"
                }`}
              >
                <td className="px-3 py-2.5 font-mono font-semibold text-text-primary">{c.callsign}</td>
                <td className="px-3 py-2.5 text-text-secondary">{c.name}</td>
                <td className="px-3 py-2.5 text-text-secondary">
                  {[c.city, c.state].filter(Boolean).join(", ")}
                </td>
                <td className="px-3 py-2.5 text-text-secondary">{c.mode}</td>
                <td className="px-3 py-2.5">
                  <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${parseStatusBadge[c.parse_status]?.cls}`}>
                    {parseStatusBadge[c.parse_status]?.label}
                  </span>
                </td>
                <td className="px-3 py-2.5">
                  <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${timingBadge[c.timing_status]?.cls}`}>
                    {timingBadge[c.timing_status]?.label}
                  </span>
                </td>
                <td className="px-3 py-2.5">
                  {c.is_new_member && <span className="text-warning" title="New member">&#9733;</span>}
                </td>
                {canEditCheckins && (
                  <td className="px-3 py-2.5">
                    <button
                      onClick={(e) => { e.stopPropagation(); onEdit(c); }}
                      className="text-text-muted hover:text-accent transition-colors p-1 rounded"
                      aria-label={`Edit check-in for ${c.callsign}`}
                      title="Edit"
                    >
                      <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                      </svg>
                    </button>
                  </td>
                )}
              </tr>
              {c.comments && (
                <tr
                  key={`${c.id}-comments`}
                  onClick={() => onSelectCheckin(isSelected ? null : c.id)}
                  className={`border-b border-border last:border-b-0 cursor-pointer transition-colors ${
                    isSelected
                      ? "bg-accent/[0.08]"
                      : c.parse_status === "manual_review"
                        ? "bg-warning/[0.04] hover:bg-bg-elevated/50"
                        : "hover:bg-bg-elevated/50"
                  }`}
                >
                  <td colSpan={canEditCheckins ? 8 : 7} className="px-3 pb-2.5 -mt-1 text-text-muted text-xs italic">
                    {c.comments}
                  </td>
                </tr>
              )}
            </React.Fragment>
            );
          })}
          {checkins.length === 0 && (
            <tr>
              <td colSpan={canEditCheckins ? 8 : 7} className="px-3 py-8 text-center text-text-muted text-sm">
                No check-ins for this session yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
```

Key changes from the original `CheckinTable`:
- Added `selectedCheckinId` and `onSelectCheckin` props
- Added `rowRefs` for scroll-into-view on map pin click
- Added `onClick` on `<tr>` for row selection (click to select, click again to deselect)
- Added `e.stopPropagation()` on the edit button so clicking edit doesn't trigger row selection
- Selected row gets `bg-accent/[0.08] border-l-2 border-l-accent` styling
- Comments moved from a truncated column to a full-width sub-row beneath each check-in (only shown when comment exists)

- [x] **Step 2: Add two-pane layout and map to CheckInsPage**

In `frontend/src/pages/CheckInsPage.tsx`, add the import for `CheckInMap` after the existing imports (after line 8):

```typescript
import { CheckInMap } from "../components/CheckInMap";
```

Add `useRef` to the React import on line 1:

```typescript
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
```

In the `CheckInsPage` function, add a new state variable after `showApproveConfirm` (line 440):

```typescript
  const [selectedCheckinId, setSelectedCheckinId] = useState<number | null>(null);
```

Replace the main content area in the return statement. Replace lines 598–608 (the `{checkinsLoading ? ... }` ternary block) with:

```tsx
      {checkinsLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : selectedSessionId ? (
        <div className="flex flex-col lg:flex-row gap-4">
          <div className="flex-1 min-w-0">
            <CheckinTable
              checkins={checkins}
              canEditCheckins={userCanEdit}
              selectedCheckinId={selectedCheckinId}
              onSelectCheckin={setSelectedCheckinId}
              onEdit={setEditingCheckin}
            />
          </div>
          <div className="flex-1 min-h-[400px]">
            <CheckInMap
              checkins={checkins}
              selectedCheckinId={selectedCheckinId}
              onSelectCheckin={setSelectedCheckinId}
            />
          </div>
        </div>
      ) : (
        <p className="text-text-muted text-sm py-4">Select a session above to view check-ins.</p>
      )}
```

Also reset `selectedCheckinId` when the session changes. In the `handleSessionChange` function (line 518), add a line:

```typescript
  const handleSessionChange = (id: number) => {
    setSelectedSessionId(id);
    setSelectedCheckinId(null);
    setSearchParams({ session: String(id) });
  };
```

- [x] **Step 3: Verify build compiles**

Run: `cd frontend && nix-shell --run "npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors

- [x] **Step 4: Commit**

```bash
git add frontend/src/pages/CheckInsPage.tsx
git commit -m "feat: integrate CheckInMap into CheckInsPage with two-pane layout and selection"
```

---

### Task 3: Remove Map Route and Nav Entry

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/layouts/Sidebar.tsx`

- [x] **Step 1: Remove the `/map` route from App.tsx**

In `frontend/src/App.tsx`, delete line 61:

```tsx
        <Route path="/map" element={<ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}><PlaceholderPage title="Map" /></ProtectedRoute>} />
```

- [x] **Step 2: Remove the Map nav entry from Sidebar.tsx**

In `frontend/src/layouts/Sidebar.tsx`, delete line 15:

```tsx
  { label: "Map", to: "/map", minRole: ["viewer", "net_control", "admin"] },
```

- [x] **Step 3: Verify build compiles**

Run: `cd frontend && nix-shell --run "npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors

- [x] **Step 4: Commit**

```bash
git add frontend/src/App.tsx frontend/src/layouts/Sidebar.tsx
git commit -m "feat: remove standalone /map route and nav entry"
```

---

### Task 4: Full Test Suite Verification

- [x] **Step 1: Run backend tests**

Run: `nix-shell --run "python -m pytest tests/ -x -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass (no backend changes, but verify nothing is broken)

- [x] **Step 2: Run frontend type check**

Run: `cd frontend && nix-shell --run "npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors
