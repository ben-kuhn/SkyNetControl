# Check-ins Map Integration Design Spec

**Goal:** Add a Leaflet map pane to the existing Check-ins page showing check-in locations as interactive pins, with bidirectional selection between the table and map.

**Architecture:** A new `CheckInMap` React component renders a Leaflet map inside the Check-ins page. The page layout changes from single-column to a two-pane split. GeoJSON data comes from the existing `/api/roster/session/{session_id}/geojson` endpoint — no new backend work. The standalone `/map` route is removed.

**Tech Stack:** Leaflet 1.9 (already installed), React, TypeScript

---

## Scope

This spec covers:
- Adding a map pane to the Check-ins page
- Bidirectional selection between table rows and map pins
- Removing the `/map` route, its nav entry, and its placeholder

This spec does NOT cover:
- New backend endpoints (the GeoJSON endpoint already exists)
- Full-screen map view (not needed)

---

## Page Layout Changes

### Two-Pane Split

The Check-ins page becomes a two-pane layout:

- **Desktop (>=1024px):** Flex row, two equal columns (50/50). Table on left, map on right.
- **Mobile (<1024px):** Flex column, stacked. Table on top, map below.

The session selector, action bar, and stats bar remain full-width above both panes.

### Structure

```
┌─────────────────────────────────────────┐
│ Session Selector                        │
│ Action Bar (scan, add, approve)         │
│ Stats Bar                               │
├────────────────────┬────────────────────┤
│                    │                    │
│   Check-ins Table  │   Leaflet Map     │
│                    │                    │
│                    │                    │
└────────────────────┴────────────────────┘
```

On mobile, the map pane sits below the table with a fixed height of 400px.

---

## Map Component

### `CheckInMap`

A new component at `frontend/src/components/CheckInMap.tsx`.

**Props:**

| Prop | Type | Description |
|------|------|-------------|
| `checkins` | `CheckIn[]` | Current session's check-ins (filters to those with lat/lon) |
| `selectedCheckinId` | `number \| null` | Currently selected check-in ID |
| `onSelectCheckin` | `(id: number) => void` | Callback when a map pin is clicked |

**Behavior:**

- Renders a Leaflet map with CartoDB Dark Matter tile layer (`https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png`). Attribution: `© OpenStreetMap contributors © CARTO`.
- Filters check-ins to those with non-null `latitude` and `longitude`.
- Renders each check-in as a `CircleMarker`:
  - **Default:** radius 6, accent color fill, semi-transparent
  - **New member** (`is_new_member === true`): radius 6, warning color fill (matches the star in the table)
  - **Selected:** radius 10, bright accent color, full opacity, white border, opens a popup with callsign (bold, mono) and name
- When `checkins` change (new session loaded), auto-fits map bounds to show all pins with some padding. If no check-ins have coordinates, show a default US-centered view.
- When `selectedCheckinId` changes (table row clicked), pans to that pin, applies the selected style, and opens its popup.
- When a pin is clicked, calls `onSelectCheckin(id)` so the parent can highlight the table row.

### Tile Layer

CartoDB Dark Matter — free, no API key, dark theme that matches the app's aesthetic.

Fallback: if tiles fail to load, the map still renders with Leaflet's default gray background. No error handling needed beyond Leaflet's built-in behavior.

---

## Table Interaction Changes

### Row Selection

The check-ins table gains a "selected" state:

- Clicking a table row sets `selectedCheckinId` in the parent `CheckInsPage` state.
- The selected row gets a subtle accent highlight (e.g., `bg-accent/[0.08] border-l-2 border-accent`).
- Clicking the same row again deselects it (sets `selectedCheckinId` to `null`).
- The edit button still works independently — clicking edit opens the modal without affecting map selection.

### Scroll Into View

When a map pin is clicked and `onSelectCheckin` fires, the table scrolls the corresponding row into view using `element.scrollIntoView({ behavior: "smooth", block: "nearest" })`.

---

## GeoJSON Endpoint

Already exists: `GET /api/roster/session/{session_id}/geojson`

Returns a GeoJSON `FeatureCollection` with `Point` features. Properties include `callsign`, `name`, `is_new_member`.

The map component does NOT use this endpoint directly. Instead, it receives the already-loaded `checkins` array (which includes `latitude` and `longitude` fields) and renders pins from that data. This avoids an extra API call and keeps the data in sync with the table.

---

## Route and Nav Removal

### Remove `/map` Route

In `frontend/src/App.tsx`, delete the `/map` route line:
```tsx
<Route path="/map" element={...}><PlaceholderPage title="Map" /></Route>
```

### Remove Map Nav Entry

In `frontend/src/layouts/Sidebar.tsx`, remove the Map item from the `navItems` array:
```tsx
{ label: "Map", to: "/map", minRole: ["viewer", "net_control", "admin"] }
```

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/components/CheckInMap.tsx` | Leaflet map component with pin rendering and selection |

**Modified files:**

| File | Change |
|------|--------|
| `frontend/src/pages/CheckInsPage.tsx` | Add two-pane layout, selection state, integrate `CheckInMap` |
| `frontend/src/App.tsx` | Remove `/map` route |
| `frontend/src/layouts/Sidebar.tsx` | Remove Map nav entry |

---

## Testing Strategy

- **Manual browser testing:** Verify map renders, pins appear, selection works bidirectionally, responsive layout at different breakpoints.
- **No new unit tests:** Leaflet rendering is inherently DOM/canvas-based and not suited to the project's existing test setup. The GeoJSON endpoint already has backend tests.
