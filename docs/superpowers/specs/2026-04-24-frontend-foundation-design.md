# Frontend Foundation — Design Spec

**Goal:** Build the frontend foundation for SkyNetControl: Tailwind-based theme system (dark/light), layout shell with responsive navigation, React Router with role-based route protection, OAuth login flow, user registration, and a read-only schedule page. Establishes the skeleton that all subsequent frontend sub-projects build on.

**Architecture:** Single-page React app served by the existing FastAPI backend. Vite dev server proxies `/api` to the backend. No component library — hand-written components styled with Tailwind CSS utility classes. Two-theme system (dark default, light option) via CSS custom properties. Auth state managed through React Context backed by the existing cookie-based JWT.

**Tech Stack:** React 18, TypeScript, Vite, Tailwind CSS v4, React Router v7, Leaflet (installed now, used in later sub-projects)

---

## Dependencies

**Runtime (added to `dependencies`):**
- `react-router-dom` — client-side routing

**Runtime (added to `dependencies`, used in later sub-projects):**
- `leaflet` — map rendering

**Dev (added to `devDependencies`):**
- `tailwindcss` — utility-first CSS framework (v4)
- `@tailwindcss/vite` — Tailwind v4 Vite plugin
- `@types/leaflet` — Leaflet type definitions

**Supply chain note:** These are the only new packages. No component library, no CSS-in-JS runtime, no icon packs. Total new runtime dependencies: 2 packages (react-router-dom, leaflet). Both are mature, well-audited, minimal transitive dependency trees.

---

## File Structure

```
frontend/src/
  main.tsx                  # Entry point, mounts App
  App.tsx                   # ThemeProvider + AuthProvider + Router
  api/
    client.ts               # Base fetch helper (handles errors, JSON parsing)
    auth.ts                 # Auth API: me, logout, register, providers
    schedule.ts             # Schedule API: list sessions
  components/
    Button.tsx              # Primary, secondary, danger variants
    Input.tsx               # Text input with label + error state
    Modal.tsx               # Dialog overlay with close button
    Toast.tsx               # Notification toast (success, error, info)
    Spinner.tsx             # Loading spinner
    ThemeToggle.tsx         # Dark/light mode switch
  layouts/
    AppShell.tsx            # Sidebar + top bar + content area orchestrator
    Sidebar.tsx             # Desktop sidebar navigation
    MobileMenu.tsx          # Full-screen hamburger menu overlay
  pages/
    LoginPage.tsx           # OAuth provider picker
    RegisterPage.tsx        # Callsign entry for PENDING users
    PendingPage.tsx         # "Awaiting approval" + read-only schedule
    SchedulePage.tsx        # Read-only upcoming session list
    ProfilePage.tsx         # User profile + callsign change + PAT placeholder
    NotFoundPage.tsx        # 404 page
    PlaceholderPage.tsx     # Generic "coming soon" page for unbuilt features
  hooks/
    useAuth.ts              # Auth state hook (reads from AuthContext)
    useTheme.ts             # Theme state hook (reads from ThemeContext)
  context/
    AuthContext.tsx          # Auth context provider, fetches /api/auth/me
    ThemeContext.tsx         # Theme context provider, manages localStorage
    ToastContext.tsx         # Toast notification state + useToast hook
  types/
    index.ts                # Shared TypeScript types (User, Session, Provider, etc.)
  styles/
    app.css                 # Tailwind directives + CSS custom properties for themes
```

---

## Theme System

Two modes: **Dark** (default) and **Light**, toggled via a button in the sidebar footer.

### Implementation

- CSS custom properties on `<html>` element, scoped by `data-theme="dark"` or `data-theme="light"` attribute
- Tailwind v4 `@theme` directive references these custom properties
- User preference stored in `localStorage` key `skynet-theme`
- On page load: check `localStorage`, fall back to `dark`, apply before first paint (inline script in `index.html` to prevent flash)

### Dark Mode (Default) — Ham Radio Polished

| Token | Value | Usage |
|-------|-------|-------|
| `--bg-base` | `#0c1222` | Page background |
| `--bg-surface` | `#0f172a` | Cards, panels, sidebar |
| `--bg-elevated` | `#1e293b` | Hover states, elevated surfaces |
| `--text-primary` | `#f1f5f9` | Headings, important text |
| `--text-secondary` | `#cbd5e1` | Body text |
| `--text-muted` | `#64748b` | Placeholder, disabled text |
| `--border` | `#1e293b` | Borders, dividers |
| `--accent` | `#22d3ee` | Primary actions, active nav, links |
| `--accent-hover` | `#06b6d4` | Accent hover state |
| `--success` | `#22c55e` | Active indicators, success states |
| `--warning` | `#fbbf24` | Warnings, pending states |
| `--danger` | `#ef4444` | Errors, destructive actions |

Visual characteristics: deep navy backgrounds, subtle gradient on nav header, cyan accent for interactivity, green status dots, monospace font for data values.

### Light Mode

| Token | Value | Usage |
|-------|-------|-------|
| `--bg-base` | `#ffffff` | Page background |
| `--bg-surface` | `#f8fafc` | Cards, panels, sidebar |
| `--bg-elevated` | `#f1f5f9` | Hover states, elevated surfaces |
| `--text-primary` | `#0f172a` | Headings, important text |
| `--text-secondary` | `#334155` | Body text |
| `--text-muted` | `#94a3b8` | Placeholder, disabled text |
| `--border` | `#e2e8f0` | Borders, dividers |
| `--accent` | `#0891b2` | Primary actions (darker cyan for contrast) |
| `--accent-hover` | `#0e7490` | Accent hover state |
| `--success` | `#16a34a` | Active indicators |
| `--warning` | `#d97706` | Warnings |
| `--danger` | `#dc2626` | Errors |

### Typography Rule

Body text uses `system-ui, -apple-system, sans-serif`. Callsigns, frequencies, timestamps, PAT tokens, and other data values use `'SF Mono', 'Cascadia Code', 'JetBrains Mono', 'Fira Code', monospace`. This is enforced via a Tailwind utility class `font-mono` applied to data elements. The monospace treatment reinforces the ham radio identity without making the whole app monospace.

---

## Layout Shell

### Desktop (≥768px)

- Fixed sidebar on the left, 240px wide, full viewport height
- Sidebar structure (top to bottom):
  - **Header:** App name "SkyNetControl" in accent color + green status dot
  - **Nav links:** Text labels, highlighted active route with left border accent and subtle background
  - **Footer:** Theme toggle, user callsign (links to `/profile`), logout button
- Content area fills remaining width, scrollable, padded

### Mobile (<768px)

- Sidebar hidden
- Top bar: hamburger button (left), "SkyNetControl" (center), user callsign (right)
- Hamburger opens a full-screen overlay with the same nav links
- Tapping a link navigates and closes the overlay
- Overlay has a close button (X) in the top right

### Navigation Items by Role

| Nav Item | Route | PENDING | VIEWER | NET_CONTROL | ADMIN |
|----------|-------|---------|--------|-------------|-------|
| Schedule | `/schedule` | read-only | read-only | full | full |
| Check-ins | `/checkins` | — | read-only | full | full |
| Map | `/map` | — | view | view | view |
| Reminders | `/reminders` | — | — | full | full |
| Roster | `/roster` | — | — | full | full |
| Activities | `/activities` | — | — | — | placeholder |
| Users | `/users` | — | — | — | full |
| Config | `/config` | — | — | — | full |

"full" and "read-only" distinctions are enforced by later sub-projects. In this spec, all pages except Login, Register, Pending, Schedule, and Profile are placeholder pages. The nav items and route guards are wired up now so later sub-projects just replace the placeholder content.

---

## Auth Flow

### Login Page (`/login`)

- Unauthenticated users are redirected here
- Fetches `GET /api/auth/providers` to get list of enabled providers
- Renders a button per provider: "Sign in with {label}" (e.g., "Sign in with Google")
- Clicking a button navigates the browser to `/api/auth/login/{provider}` (full page navigation, not fetch — the backend handles the OAuth redirect chain)
- After OAuth callback, the backend sets the `access_token` cookie and redirects to `/`
- The app then fetches `/api/auth/me` to load user state

### Post-Login Routing

`AuthContext` fetches `GET /api/auth/me` on mount. Based on the response:

| Condition | Redirect |
|-----------|----------|
| No cookie / 401 | `/login` |
| PENDING role + callsign starts with "PENDING-" | `/register` |
| PENDING role + real callsign | `/pending` |
| VIEWER / NET_CONTROL / ADMIN | `/schedule` |

### Registration Page (`/register`)

- Only accessible to PENDING users with placeholder callsigns
- Single input: callsign (auto-uppercased)
- Client-side validation: matches `^[A-Z]{1,2}\d[A-Z]{1,4}$`
- Submit calls `POST /api/auth/register` with `{ "callsign": "W0ABC" }`
- On success: redirects to `/pending`
- On error (409 taken, 400 invalid): shows inline error message

### Pending Page (`/pending`)

- Shows: "Your account ({callsign}) is awaiting admin approval"
- Displays read-only schedule below (same component as SchedulePage)
- Polls `GET /api/auth/me` every 30 seconds
- When role changes from PENDING: redirects to `/schedule` and shows a success toast "Your account has been approved!"

### Route Protection

- `ProtectedRoute` component wraps all authenticated routes
- Checks `AuthContext` for current user
- If unauthenticated: redirect to `/login`
- If user lacks required role for the route: redirect to `/schedule`
- PENDING users can only access `/register`, `/pending`, and `/schedule` (read-only)

### Logout

- "Logout" button in sidebar footer
- Calls `POST /api/auth/logout`
- Clears `AuthContext` state
- Redirects to `/login`

---

## Pages Built in This Spec

### Schedule Page (`/schedule`) — Read-Only

- Default landing page for authenticated users
- Fetches `GET /api/schedule/sessions?status=scheduled` for upcoming sessions
- Displays sessions as a list of cards, each showing:
  - Start date (and end date if set)
  - Session type
  - Net control operator callsign (monospace)
  - Status badge
  - Grace period
- No create/edit/delete controls (added in Net Operations sub-project)
- Empty state: "No upcoming sessions scheduled"

### Profile Page (`/profile`)

- Accessible to all authenticated users (VIEWER, NET_CONTROL, ADMIN — not PENDING)
- Displays: callsign (large, monospace), name, email, role badge
- **Callsign change section:**
  - If `pending_callsign` exists: shows "Pending approval: {pending_callsign}" with muted styling
  - If no pending change: input field + "Request Change" button
  - Validates format client-side, submits `PATCH /api/auth/me`
  - On success: shows the pending callsign state
- **PAT section:**
  - Heading: "Personal Access Tokens"
  - Placeholder message: "Token management is coming soon."

### Placeholder Page (generic)

- Used for all unbuilt routes: `/checkins`, `/map`, `/reminders`, `/roster`, `/activities`, `/users`, `/config`
- Shows page title and "This feature is under development."
- Role guards still enforced — a VIEWER can't access `/users` even though it's a placeholder

### Not Found Page (`/404`)

- Shown for unrecognized routes
- "Page not found" with a link back to `/schedule`

---

## Shared Components

### Button

- Variants: `primary` (accent color), `secondary` (muted), `danger` (red)
- Props: `variant`, `size` (`sm`, `md`), `loading` (shows spinner), `disabled`, `onClick`, `type`, `children`
- Full-width option for forms

### Input

- Props: `label`, `error` (error message string), `value`, `onChange`, `placeholder`, `type`, `autoFocus`
- Label above the input, error message below in danger color
- Monospace variant for callsign inputs

### Modal

- Props: `open`, `onClose`, `title`, `children`
- Overlay with centered card, close button (X), click-outside-to-close
- Focus trap for accessibility

### Toast

- Props: `message`, `type` (`success`, `error`, `info`), `duration`
- Fixed position bottom-right, auto-dismiss after duration
- Toast state managed via a simple context + hook (`useToast`)

### Spinner

- Simple CSS spinner using accent color
- Props: `size` (`sm`, `md`, `lg`)

### ThemeToggle

- Button with sun icon (in dark mode) / moon icon (in light mode)
- Toggles `data-theme` on `<html>` and persists to `localStorage`
- Icons are inline SVG, not an icon library

---

## API Client Layer

### `api/client.ts`

```typescript
async function apiFetch<T>(path: string, options?: RequestInit): Promise<T>
```

- Prepends `/api` to path
- Sets `Content-Type: application/json` for POST/PATCH/PUT
- Parses JSON response
- Throws typed error on non-2xx (includes status code and detail message from backend)
- 401 responses trigger auth state clear + redirect to `/login`

### `api/auth.ts`

- `fetchMe()` → `User | null` — GET `/api/auth/me`, returns null on 401
- `fetchProviders()` → `Provider[]` — GET `/api/auth/providers`
- `register(callsign: string)` → `User` — POST `/api/auth/register`
- `updateCallsign(callsign: string)` → `User` — PATCH `/api/auth/me`
- `logout()` → void — POST `/api/auth/logout`

### `api/schedule.ts`

- `fetchSessions(params?)` → `Session[]` — GET `/api/schedule/sessions`

---

## TypeScript Types

```typescript
type UserRole = "pending" | "viewer" | "net_control" | "admin";

interface User {
  callsign: string;
  name: string;
  role: UserRole;
  email: string | null;
  pending_callsign: string | null;
}

interface Provider {
  name: string;
  label: string;
}

interface Session {
  id: number;
  season_id: number | null;
  start_date: string;
  end_date: string | null;
  grace_period_hours: number;
  session_type: string;
  status: string;
  activity_id: number | null;
  net_control_callsign: string | null;
}
```

---

## Testing

This spec does not include frontend unit tests. Testing strategy for the frontend will be defined in a separate spec. The backend is already fully tested (300 tests passing).

---

## What This Spec Does NOT Build

- **Personal Access Tokens** — separate spec, queued next, required before deployment
- **Check-in management** — Net Operations sub-project
- **Map view** — Net Operations sub-project (Leaflet installed now)
- **Reminders** — Post-Session sub-project
- **Roster** — Post-Session sub-project
- **Activities / AI chat** — Content Management sub-project
- **User management** — Admin sub-project
- **App configuration** — Admin sub-project
- **Schedule editing** — Net Operations sub-project (read-only view only in this spec)

---

## Sub-Project Queue

After this spec, the remaining frontend sub-projects and the PAT spec should be built in this order:

1. **Personal Access Tokens** (backend + frontend) — required for deployment
2. **Admin** (user management, app config) — needed for operators to manage the system
3. **Net Operations** (schedule editing, check-in tracking, map) — core workflow
4. **Post-Session** (reminders, roster review/approve/send) — post-net workflow
5. **Content Management** (activities CRUD, AI chat) — content authoring
