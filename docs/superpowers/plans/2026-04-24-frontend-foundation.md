# Frontend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the frontend foundation: Tailwind theme system (dark/light ham radio theme), responsive layout shell, React Router with role-based route protection, OAuth login flow, user registration, read-only schedule page, and profile page.

**Architecture:** Single-page React app using Vite. Tailwind CSS v4 for styling with CSS custom properties for theming. React Router v7 for client-side routing. Auth state via React Context backed by cookie-based JWT. All API calls go through a typed fetch wrapper.

**Tech Stack:** React 18, TypeScript, Vite, Tailwind CSS v4, React Router v7, Leaflet (installed, used later)

**Important — NixOS:** All shell commands MUST use `nix-shell --run "..."`. The nix-shell provides Node.js 22.x, npm, and auto-installs frontend deps. Frontend commands should be `nix-shell --run "cd frontend && ..."`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `frontend/package.json` | Add new dependencies |
| `frontend/vite.config.ts` | Add Tailwind plugin |
| `frontend/index.html` | Add theme flash-prevention script |
| `frontend/src/styles/app.css` | Tailwind directives + CSS custom properties |
| `frontend/src/types/index.ts` | Shared TypeScript types |
| `frontend/src/api/client.ts` | Base fetch helper |
| `frontend/src/api/auth.ts` | Auth API functions |
| `frontend/src/api/schedule.ts` | Schedule API functions |
| `frontend/src/context/ThemeContext.tsx` | Theme provider + useTheme hook |
| `frontend/src/context/AuthContext.tsx` | Auth provider + useAuth hook |
| `frontend/src/context/ToastContext.tsx` | Toast provider + useToast hook |
| `frontend/src/hooks/useTheme.ts` | Theme state hook |
| `frontend/src/hooks/useAuth.ts` | Auth state hook |
| `frontend/src/components/Spinner.tsx` | Loading spinner |
| `frontend/src/components/Button.tsx` | Button with variants |
| `frontend/src/components/Input.tsx` | Input with label + error |
| `frontend/src/components/Modal.tsx` | Dialog overlay |
| `frontend/src/components/Toast.tsx` | Notification toast |
| `frontend/src/components/ThemeToggle.tsx` | Dark/light toggle button |
| `frontend/src/layouts/Sidebar.tsx` | Desktop sidebar navigation |
| `frontend/src/layouts/MobileMenu.tsx` | Hamburger menu overlay |
| `frontend/src/layouts/AppShell.tsx` | Layout orchestrator |
| `frontend/src/pages/LoginPage.tsx` | OAuth provider picker |
| `frontend/src/pages/RegisterPage.tsx` | Callsign entry |
| `frontend/src/pages/PendingPage.tsx` | Awaiting approval |
| `frontend/src/pages/SchedulePage.tsx` | Read-only session list |
| `frontend/src/pages/ProfilePage.tsx` | User profile + callsign change |
| `frontend/src/pages/PlaceholderPage.tsx` | Generic "coming soon" |
| `frontend/src/pages/NotFoundPage.tsx` | 404 page |
| `frontend/src/ProtectedRoute.tsx` | Role-based route guard |
| `frontend/src/App.tsx` | Router setup with providers |
| `frontend/src/main.tsx` | Entry point (updated) |

---

### Task 1: Dependencies + Tailwind Setup

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.ts`
- Create: `frontend/src/styles/app.css`
- Modify: `frontend/index.html`
- Modify: `frontend/src/main.tsx`
- Delete: `frontend/src/App.css`

- [ ] **Step 1: Install dependencies**

Run:
```bash
nix-shell --run "cd frontend && npm install react-router-dom leaflet && npm install -D tailwindcss @tailwindcss/vite @types/leaflet"
```

- [ ] **Step 2: Update `frontend/vite.config.ts`**

Replace the entire file:

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
```

- [ ] **Step 3: Create `frontend/src/styles/app.css`**

```css
@import "tailwindcss";

@theme {
  --font-sans: system-ui, -apple-system, sans-serif;
  --font-mono: 'SF Mono', 'Cascadia Code', 'JetBrains Mono', 'Fira Code', monospace;

  --color-bg-base: var(--bg-base);
  --color-bg-surface: var(--bg-surface);
  --color-bg-elevated: var(--bg-elevated);
  --color-text-primary: var(--text-primary);
  --color-text-secondary: var(--text-secondary);
  --color-text-muted: var(--text-muted);
  --color-border: var(--border-color);
  --color-accent: var(--accent);
  --color-accent-hover: var(--accent-hover);
  --color-success: var(--success);
  --color-warning: var(--warning);
  --color-danger: var(--danger);
}

/* Dark theme (default) — Ham Radio Polished */
html[data-theme="dark"] {
  --bg-base: #0c1222;
  --bg-surface: #0f172a;
  --bg-elevated: #1e293b;
  --text-primary: #f1f5f9;
  --text-secondary: #cbd5e1;
  --text-muted: #64748b;
  --border-color: #1e293b;
  --accent: #22d3ee;
  --accent-hover: #06b6d4;
  --success: #22c55e;
  --warning: #fbbf24;
  --danger: #ef4444;
}

/* Light theme */
html[data-theme="light"] {
  --bg-base: #ffffff;
  --bg-surface: #f8fafc;
  --bg-elevated: #f1f5f9;
  --text-primary: #0f172a;
  --text-secondary: #334155;
  --text-muted: #94a3b8;
  --border-color: #e2e8f0;
  --accent: #0891b2;
  --accent-hover: #0e7490;
  --success: #16a34a;
  --warning: #d97706;
  --danger: #dc2626;
}

body {
  background-color: var(--bg-base);
  color: var(--text-secondary);
  font-family: var(--font-sans);
  margin: 0;
  min-height: 100vh;
}
```

- [ ] **Step 4: Update `frontend/index.html`**

Replace the entire file:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>SkyNetControl</title>
    <script>
      (function() {
        var theme = localStorage.getItem('skynet-theme') || 'dark';
        document.documentElement.setAttribute('data-theme', theme);
      })();
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Update `frontend/src/main.tsx`**

Replace the entire file:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/app.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 6: Replace `frontend/src/App.tsx` with a minimal placeholder**

Replace the entire file:

```tsx
export default function App() {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="text-center">
        <h1 className="text-2xl font-bold text-text-primary">SkyNetControl</h1>
        <p className="text-text-muted mt-2">Foundation loading...</p>
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Delete old CSS file**

```bash
rm frontend/src/App.css
```

- [ ] **Step 8: Verify build**

Run:
```bash
nix-shell --run "cd frontend && npx tsc -b && npx vite build" 2>&1 | tail -10
```

Expected: Build succeeds with no errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): add Tailwind v4 and dark/light theme system"
```

---

### Task 2: TypeScript Types + API Client

**Files:**
- Create: `frontend/src/types/index.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/auth.ts`
- Create: `frontend/src/api/schedule.ts`

- [ ] **Step 1: Create `frontend/src/types/index.ts`**

```typescript
export type UserRole = "pending" | "viewer" | "net_control" | "admin";

export interface User {
  callsign: string;
  name: string;
  role: UserRole;
  email: string | null;
  pending_callsign: string | null;
}

export interface Provider {
  name: string;
  label: string;
}

export interface Session {
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

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}
```

- [ ] **Step 2: Create `frontend/src/api/client.ts`**

```typescript
import { ApiError } from "../types";

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const url = `/api${path}`;

  const headers: Record<string, string> = {};
  if (
    options?.method &&
    ["POST", "PATCH", "PUT"].includes(options.method.toUpperCase()) &&
    options.body
  ) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(url, {
    ...options,
    headers: {
      ...headers,
      ...(options?.headers as Record<string, string>),
    },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // response body wasn't JSON
    }

    if (response.status === 401) {
      window.location.href = "/login";
    }

    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}
```

- [ ] **Step 3: Create `frontend/src/api/auth.ts`**

```typescript
import type { Provider, User } from "../types";
import { apiFetch } from "./client";

export async function fetchMe(): Promise<User | null> {
  try {
    return await apiFetch<User>("/auth/me");
  } catch {
    return null;
  }
}

export async function fetchProviders(): Promise<Provider[]> {
  return apiFetch<Provider[]>("/auth/providers");
}

export async function register(callsign: string): Promise<User> {
  return apiFetch<User>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ callsign }),
  });
}

export async function updateCallsign(callsign: string): Promise<User> {
  return apiFetch<User>("/auth/me", {
    method: "PATCH",
    body: JSON.stringify({ callsign }),
  });
}

export async function logout(): Promise<void> {
  await apiFetch<void>("/auth/logout", { method: "POST" });
}
```

- [ ] **Step 4: Create `frontend/src/api/schedule.ts`**

```typescript
import type { Session } from "../types";
import { apiFetch } from "./client";

export async function fetchSessions(params?: {
  status?: string;
  season_id?: number;
}): Promise<Session[]> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.season_id)
    searchParams.set("season_id", String(params.season_id));

  const query = searchParams.toString();
  return apiFetch<Session[]>(`/schedule/sessions${query ? `?${query}` : ""}`);
}
```

- [ ] **Step 5: Verify build**

Run:
```bash
nix-shell --run "cd frontend && npx tsc -b" 2>&1 | tail -10
```

Expected: No type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/ frontend/src/api/
git commit -m "feat(frontend): add TypeScript types and API client layer"
```

---

### Task 3: Context Providers (Theme, Auth, Toast)

**Files:**
- Create: `frontend/src/context/ThemeContext.tsx`
- Create: `frontend/src/context/AuthContext.tsx`
- Create: `frontend/src/context/ToastContext.tsx`
- Create: `frontend/src/hooks/useTheme.ts`
- Create: `frontend/src/hooks/useAuth.ts`

- [ ] **Step 1: Create `frontend/src/context/ThemeContext.tsx`**

```tsx
import { createContext, useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

type Theme = "dark" | "light";

interface ThemeContextValue {
  theme: Theme;
  toggleTheme: () => void;
}

export const ThemeContext = createContext<ThemeContextValue>({
  theme: "dark",
  toggleTheme: () => {},
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    return (localStorage.getItem("skynet-theme") as Theme) || "dark";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("skynet-theme", theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
```

- [ ] **Step 2: Create `frontend/src/hooks/useTheme.ts`**

```typescript
import { useContext } from "react";
import { ThemeContext } from "../context/ThemeContext";

export function useTheme() {
  return useContext(ThemeContext);
}
```

- [ ] **Step 3: Create `frontend/src/context/AuthContext.tsx`**

```tsx
import { createContext, useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { User } from "../types";
import { fetchMe, logout as apiLogout } from "../api/auth";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  refreshUser: () => Promise<void>;
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  refreshUser: async () => {},
  logout: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    const me = await fetchMe();
    setUser(me);
    setLoading(false);
  }, []);

  useEffect(() => {
    refreshUser();
  }, [refreshUser]);

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, refreshUser, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
```

- [ ] **Step 4: Create `frontend/src/hooks/useAuth.ts`**

```typescript
import { useContext } from "react";
import { AuthContext } from "../context/AuthContext";

export function useAuth() {
  return useContext(AuthContext);
}
```

- [ ] **Step 5: Create `frontend/src/context/ToastContext.tsx`**

```tsx
import { createContext, useCallback, useContext, useState } from "react";
import type { ReactNode } from "react";

type ToastType = "success" | "error" | "info";

interface ToastMessage {
  id: number;
  message: string;
  type: ToastType;
}

interface ToastContextValue {
  toasts: ToastMessage[];
  addToast: (message: string, type?: ToastType, duration?: number) => void;
  removeToast: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue>({
  toasts: [],
  addToast: () => {},
  removeToast: () => {},
});

let nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const removeToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (message: string, type: ToastType = "info", duration = 4000) => {
      const id = nextId++;
      setToasts((prev) => [...prev, { id, message, type }]);
      setTimeout(() => removeToast(id), duration);
    },
    [removeToast],
  );

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast }}>
      {children}
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
```

- [ ] **Step 6: Verify build**

Run:
```bash
nix-shell --run "cd frontend && npx tsc -b" 2>&1 | tail -10
```

Expected: No type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/context/ frontend/src/hooks/
git commit -m "feat(frontend): add theme, auth, and toast context providers"
```

---

### Task 4: Shared Components

**Files:**
- Create: `frontend/src/components/Spinner.tsx`
- Create: `frontend/src/components/Button.tsx`
- Create: `frontend/src/components/Input.tsx`
- Create: `frontend/src/components/Modal.tsx`
- Create: `frontend/src/components/Toast.tsx`
- Create: `frontend/src/components/ThemeToggle.tsx`

- [ ] **Step 1: Create `frontend/src/components/Spinner.tsx`**

```tsx
interface SpinnerProps {
  size?: "sm" | "md" | "lg";
}

const sizes = {
  sm: "h-4 w-4 border-2",
  md: "h-6 w-6 border-2",
  lg: "h-10 w-10 border-3",
};

export function Spinner({ size = "md" }: SpinnerProps) {
  return (
    <div
      className={`${sizes[size]} animate-spin rounded-full border-accent border-t-transparent`}
      role="status"
    >
      <span className="sr-only">Loading...</span>
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/src/components/Button.tsx`**

```tsx
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
  fullWidth?: boolean;
  children: ReactNode;
}

const variantClasses = {
  primary:
    "bg-accent text-bg-base hover:bg-accent-hover focus:ring-accent/50",
  secondary:
    "bg-bg-elevated text-text-secondary hover:bg-border border border-border focus:ring-border/50",
  danger:
    "bg-danger text-white hover:bg-danger/80 focus:ring-danger/50",
};

const sizeClasses = {
  sm: "px-3 py-1.5 text-sm",
  md: "px-4 py-2 text-sm",
};

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  fullWidth = false,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`
        inline-flex items-center justify-center gap-2 rounded-md font-medium
        transition-colors focus:outline-none focus:ring-2
        disabled:opacity-50 disabled:cursor-not-allowed
        ${variantClasses[variant]}
        ${sizeClasses[size]}
        ${fullWidth ? "w-full" : ""}
      `}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Spinner size="sm" />}
      {children}
    </button>
  );
}
```

- [ ] **Step 3: Create `frontend/src/components/Input.tsx`**

```tsx
import type { InputHTMLAttributes } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  mono?: boolean;
}

export function Input({ label, error, mono, className, ...props }: InputProps) {
  return (
    <div className="flex flex-col gap-1">
      {label && (
        <label className="text-sm font-medium text-text-secondary">
          {label}
        </label>
      )}
      <input
        className={`
          rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm
          text-text-primary placeholder:text-text-muted
          focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent
          ${mono ? "font-mono" : ""}
          ${error ? "border-danger" : ""}
          ${className || ""}
        `}
        {...props}
      />
      {error && <p className="text-sm text-danger">{error}</p>}
    </div>
  );
}
```

- [ ] **Step 4: Create `frontend/src/components/Modal.tsx`**

```tsx
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}

export function Modal({ open, onClose, title, children }: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}
    >
      <div className="w-full max-w-md rounded-lg bg-bg-surface border border-border p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary transition-colors"
          >
            <svg
              className="h-5 w-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Create `frontend/src/components/Toast.tsx`**

```tsx
import { useToast } from "../context/ToastContext";

const typeClasses = {
  success: "border-success text-success",
  error: "border-danger text-danger",
  info: "border-accent text-accent",
};

export function ToastContainer() {
  const { toasts, removeToast } = useToast();

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`
            rounded-md border bg-bg-surface px-4 py-3 text-sm shadow-lg
            flex items-center gap-3
            ${typeClasses[toast.type]}
          `}
        >
          <span className="text-text-secondary">{toast.message}</span>
          <button
            onClick={() => removeToast(toast.id)}
            className="text-text-muted hover:text-text-primary ml-auto"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 6: Create `frontend/src/components/ThemeToggle.tsx`**

```tsx
import { useTheme } from "../hooks/useTheme";

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();

  return (
    <button
      onClick={toggleTheme}
      className="p-2 rounded-md text-text-muted hover:text-text-primary hover:bg-bg-elevated transition-colors"
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
        </svg>
      ) : (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
        </svg>
      )}
    </button>
  );
}
```

- [ ] **Step 7: Verify build**

Run:
```bash
nix-shell --run "cd frontend && npx tsc -b" 2>&1 | tail -10
```

Expected: No type errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/
git commit -m "feat(frontend): add shared UI components"
```

---

### Task 5: Layout Shell (Sidebar + Mobile Menu + AppShell)

**Files:**
- Create: `frontend/src/layouts/Sidebar.tsx`
- Create: `frontend/src/layouts/MobileMenu.tsx`
- Create: `frontend/src/layouts/AppShell.tsx`

- [ ] **Step 1: Create `frontend/src/layouts/Sidebar.tsx`**

```tsx
import { NavLink } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ThemeToggle } from "../components/ThemeToggle";
import type { UserRole } from "../types";

interface NavItem {
  label: string;
  to: string;
  minRole: UserRole[];
}

const navItems: NavItem[] = [
  { label: "Schedule", to: "/schedule", minRole: ["pending", "viewer", "net_control", "admin"] },
  { label: "Check-ins", to: "/checkins", minRole: ["viewer", "net_control", "admin"] },
  { label: "Map", to: "/map", minRole: ["viewer", "net_control", "admin"] },
  { label: "Reminders", to: "/reminders", minRole: ["net_control", "admin"] },
  { label: "Roster", to: "/roster", minRole: ["net_control", "admin"] },
  { label: "Activities", to: "/activities", minRole: ["admin"] },
  { label: "Users", to: "/users", minRole: ["admin"] },
  { label: "Config", to: "/config", minRole: ["admin"] },
];

export function Sidebar() {
  const { user, logout } = useAuth();

  const visibleItems = navItems.filter(
    (item) => user && item.minRole.includes(user.role),
  );

  return (
    <aside className="hidden md:flex md:flex-col md:w-60 md:fixed md:inset-y-0 bg-bg-surface border-r border-border">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-4 border-b border-border bg-linear-to-b from-bg-elevated to-bg-surface">
        <div className="h-2 w-2 rounded-full bg-success shadow-[0_0_6px_rgba(34,197,94,0.5)]" />
        <span className="font-bold text-accent tracking-wide">SkyNetControl</span>
      </div>

      {/* Nav links */}
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {visibleItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `block px-3 py-2 rounded-md text-sm mb-0.5 transition-colors ${
                isActive
                  ? "text-accent bg-accent/10 border-l-2 border-accent pl-2.5"
                  : "text-text-muted hover:text-text-primary hover:bg-bg-elevated"
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-border px-3 py-3 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <ThemeToggle />
          <NavLink
            to="/profile"
            className="font-mono text-sm text-text-secondary hover:text-accent transition-colors"
          >
            {user?.callsign}
          </NavLink>
        </div>
        <button
          onClick={logout}
          className="w-full text-left px-2 py-1.5 text-sm text-text-muted hover:text-danger transition-colors rounded-md hover:bg-bg-elevated"
        >
          Logout
        </button>
      </div>
    </aside>
  );
}
```

- [ ] **Step 2: Create `frontend/src/layouts/MobileMenu.tsx`**

```tsx
import { NavLink } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ThemeToggle } from "../components/ThemeToggle";
import type { UserRole } from "../types";

interface NavItem {
  label: string;
  to: string;
  minRole: UserRole[];
}

const navItems: NavItem[] = [
  { label: "Schedule", to: "/schedule", minRole: ["pending", "viewer", "net_control", "admin"] },
  { label: "Check-ins", to: "/checkins", minRole: ["viewer", "net_control", "admin"] },
  { label: "Map", to: "/map", minRole: ["viewer", "net_control", "admin"] },
  { label: "Reminders", to: "/reminders", minRole: ["net_control", "admin"] },
  { label: "Roster", to: "/roster", minRole: ["net_control", "admin"] },
  { label: "Activities", to: "/activities", minRole: ["admin"] },
  { label: "Users", to: "/users", minRole: ["admin"] },
  { label: "Config", to: "/config", minRole: ["admin"] },
];

interface MobileMenuProps {
  open: boolean;
  onClose: () => void;
}

export function MobileMenu({ open, onClose }: MobileMenuProps) {
  const { user, logout } = useAuth();

  if (!open) return null;

  const visibleItems = navItems.filter(
    (item) => user && item.minRole.includes(user.role),
  );

  return (
    <div className="fixed inset-0 z-40 bg-bg-base md:hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <span className="font-bold text-accent">SkyNetControl</span>
        <button
          onClick={onClose}
          className="p-2 text-text-muted hover:text-text-primary"
        >
          <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Nav links */}
      <nav className="px-4 py-4 flex flex-col gap-1">
        {visibleItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={onClose}
            className={({ isActive }) =>
              `block px-3 py-3 rounded-md text-base transition-colors ${
                isActive
                  ? "text-accent bg-accent/10"
                  : "text-text-secondary hover:text-text-primary hover:bg-bg-elevated"
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="absolute bottom-0 left-0 right-0 border-t border-border px-4 py-4">
        <div className="flex items-center justify-between mb-3">
          <NavLink
            to="/profile"
            onClick={onClose}
            className="font-mono text-sm text-text-secondary hover:text-accent"
          >
            {user?.callsign}
          </NavLink>
          <ThemeToggle />
        </div>
        <button
          onClick={() => { logout(); onClose(); }}
          className="w-full text-left px-2 py-2 text-sm text-text-muted hover:text-danger transition-colors rounded-md hover:bg-bg-elevated"
        >
          Logout
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/layouts/AppShell.tsx`**

```tsx
import { useState } from "react";
import { Outlet } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { Sidebar } from "./Sidebar";
import { MobileMenu } from "./MobileMenu";

export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { user } = useAuth();

  return (
    <div className="min-h-screen bg-bg-base">
      <Sidebar />
      <MobileMenu open={menuOpen} onClose={() => setMenuOpen(false)} />

      {/* Mobile top bar */}
      <div className="md:hidden flex items-center justify-between px-4 py-3 border-b border-border bg-bg-surface">
        <button
          onClick={() => setMenuOpen(true)}
          className="p-1 text-text-muted hover:text-text-primary"
        >
          <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <span className="font-bold text-accent text-sm">SkyNetControl</span>
        <span className="font-mono text-xs text-text-muted">{user?.callsign}</span>
      </div>

      {/* Main content */}
      <main className="md:ml-60 p-4 md:p-6">
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 4: Verify build**

Run:
```bash
nix-shell --run "cd frontend && npx tsc -b" 2>&1 | tail -10
```

Expected: No type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/layouts/
git commit -m "feat(frontend): add layout shell with sidebar and mobile menu"
```

---

### Task 6: Pages

**Files:**
- Create: `frontend/src/pages/LoginPage.tsx`
- Create: `frontend/src/pages/RegisterPage.tsx`
- Create: `frontend/src/pages/PendingPage.tsx`
- Create: `frontend/src/pages/SchedulePage.tsx`
- Create: `frontend/src/pages/ProfilePage.tsx`
- Create: `frontend/src/pages/PlaceholderPage.tsx`
- Create: `frontend/src/pages/NotFoundPage.tsx`

- [ ] **Step 1: Create `frontend/src/pages/LoginPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { fetchProviders } from "../api/auth";
import { Button } from "../components/Button";
import { Spinner } from "../components/Spinner";
import type { Provider } from "../types";

export function LoginPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProviders()
      .then(setProviders)
      .catch(() => setError("Failed to load login providers"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-bg-base flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="flex items-center justify-center gap-2 mb-2">
            <div className="h-2 w-2 rounded-full bg-success shadow-[0_0_6px_rgba(34,197,94,0.5)]" />
            <h1 className="text-2xl font-bold text-accent tracking-wide">
              SkyNetControl
            </h1>
          </div>
          <p className="text-text-muted text-sm">Winlink Net Management</p>
        </div>

        <div className="bg-bg-surface border border-border rounded-lg p-6">
          <h2 className="text-lg font-semibold text-text-primary mb-4 text-center">
            Sign In
          </h2>

          {loading && (
            <div className="flex justify-center py-4">
              <Spinner />
            </div>
          )}

          {error && (
            <p className="text-danger text-sm text-center mb-4">{error}</p>
          )}

          {!loading && !error && providers.length === 0 && (
            <p className="text-text-muted text-sm text-center">
              No login providers are configured. Contact the administrator.
            </p>
          )}

          <div className="flex flex-col gap-3">
            {providers.map((provider) => (
              <Button
                key={provider.name}
                variant="secondary"
                fullWidth
                onClick={() => {
                  window.location.href = `/api/auth/login/${provider.name}`;
                }}
              >
                Sign in with {provider.label}
              </Button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/src/pages/RegisterPage.tsx`**

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { register } from "../api/auth";
import { useAuth } from "../hooks/useAuth";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { ApiError } from "../types";

const CALLSIGN_PATTERN = /^[A-Z]{1,2}\d[A-Z]{1,4}$/;

export function RegisterPage() {
  const [callsign, setCallsign] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { refreshUser } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const upper = callsign.toUpperCase();
    if (!CALLSIGN_PATTERN.test(upper)) {
      setError("Invalid callsign format (e.g., W0NE, KD0ABC)");
      return;
    }

    setLoading(true);
    try {
      await register(upper);
      await refreshUser();
      navigate("/pending");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError("Registration failed");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-bg-base flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-accent">Register</h1>
          <p className="text-text-muted text-sm mt-2">
            Enter your amateur radio callsign to get started.
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-bg-surface border border-border rounded-lg p-6"
        >
          <Input
            label="Callsign"
            value={callsign}
            onChange={(e) => setCallsign(e.target.value.toUpperCase())}
            placeholder="W0ABC"
            error={error || undefined}
            mono
            autoFocus
          />

          <Button
            type="submit"
            fullWidth
            loading={loading}
            className="mt-4"
          >
            Register
          </Button>
        </form>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/pages/PendingPage.tsx`**

```tsx
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../context/ToastContext";
import { ScheduleList } from "./SchedulePage";

export function PendingPage() {
  const { user, refreshUser } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  useEffect(() => {
    const interval = setInterval(async () => {
      await refreshUser();
    }, 30000);
    return () => clearInterval(interval);
  }, [refreshUser]);

  useEffect(() => {
    if (user && user.role !== "pending") {
      addToast("Your account has been approved!", "success");
      navigate("/schedule");
    }
  }, [user, addToast, navigate]);

  return (
    <div className="min-h-screen bg-bg-base p-4">
      <div className="max-w-2xl mx-auto">
        <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
          <div className="flex items-center gap-3 mb-2">
            <div className="h-3 w-3 rounded-full bg-warning animate-pulse" />
            <h1 className="text-xl font-bold text-text-primary">
              Awaiting Approval
            </h1>
          </div>
          <p className="text-text-secondary text-sm">
            Your account{" "}
            <span className="font-mono text-accent">{user?.callsign}</span>{" "}
            is awaiting admin approval. You can view the net schedule below while
            you wait.
          </p>
        </div>

        <h2 className="text-lg font-semibold text-text-primary mb-4">
          Upcoming Sessions
        </h2>
        <ScheduleList />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create `frontend/src/pages/SchedulePage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { fetchSessions } from "../api/schedule";
import { Spinner } from "../components/Spinner";
import type { Session } from "../types";

export function ScheduleList() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchSessions({ status: "scheduled" })
      .then(setSessions)
      .catch(() => setError("Failed to load sessions"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  if (error) {
    return <p className="text-danger text-sm">{error}</p>;
  }

  if (sessions.length === 0) {
    return (
      <p className="text-text-muted text-sm py-4">
        No upcoming sessions scheduled.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {sessions.map((session) => (
        <div
          key={session.id}
          className="bg-bg-surface border border-border rounded-lg p-4"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="font-mono text-text-primary text-sm">
                {new Date(session.start_date).toLocaleDateString(undefined, {
                  weekday: "short",
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                })}
              </div>
              {session.end_date && (
                <div className="text-text-muted text-xs mt-0.5">
                  through{" "}
                  {new Date(session.end_date).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                  })}
                </div>
              )}
            </div>
            <span
              className={`
                text-xs px-2 py-0.5 rounded font-medium
                ${
                  session.status === "scheduled"
                    ? "bg-accent/10 text-accent border border-accent/25"
                    : session.status === "completed"
                      ? "bg-success/10 text-success border border-success/25"
                      : "bg-warning/10 text-warning border border-warning/25"
                }
              `}
            >
              {session.status}
            </span>
          </div>

          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
            <span>Type: {session.session_type.replace("_", " ")}</span>
            {session.net_control_callsign && (
              <span>
                NCS:{" "}
                <span className="font-mono text-text-secondary">
                  {session.net_control_callsign}
                </span>
              </span>
            )}
            <span>Grace: {session.grace_period_hours}h</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function SchedulePage() {
  return (
    <div>
      <h1 className="text-xl font-bold text-text-primary mb-4">
        Net Schedule
      </h1>
      <ScheduleList />
    </div>
  );
}
```

- [ ] **Step 5: Create `frontend/src/pages/ProfilePage.tsx`**

```tsx
import { useState } from "react";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../context/ToastContext";
import { updateCallsign } from "../api/auth";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { ApiError } from "../types";

const CALLSIGN_PATTERN = /^[A-Z]{1,2}\d[A-Z]{1,4}$/;

export function ProfilePage() {
  const { user, refreshUser } = useAuth();
  const { addToast } = useToast();
  const [newCallsign, setNewCallsign] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (!user) return null;

  const handleCallsignChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const upper = newCallsign.toUpperCase();
    if (!CALLSIGN_PATTERN.test(upper)) {
      setError("Invalid callsign format (e.g., W0NE, KD0ABC)");
      return;
    }

    setLoading(true);
    try {
      await updateCallsign(upper);
      await refreshUser();
      setNewCallsign("");
      addToast("Callsign change request submitted", "success");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError("Request failed");
      }
    } finally {
      setLoading(false);
    }
  };

  const roleBadgeClass =
    user.role === "admin"
      ? "bg-accent/10 text-accent border-accent/25"
      : user.role === "net_control"
        ? "bg-success/10 text-success border-success/25"
        : "bg-bg-elevated text-text-muted border-border";

  return (
    <div className="max-w-lg">
      <h1 className="text-xl font-bold text-text-primary mb-6">Profile</h1>

      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
        <div className="font-mono text-2xl text-accent mb-1">
          {user.callsign}
        </div>
        <div className="text-text-secondary">{user.name}</div>
        {user.email && (
          <div className="text-text-muted text-sm mt-1">{user.email}</div>
        )}
        <span
          className={`inline-block mt-2 text-xs px-2 py-0.5 rounded border ${roleBadgeClass}`}
        >
          {user.role.replace("_", " ")}
        </span>
      </div>

      {/* Callsign change */}
      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
        <h2 className="text-lg font-semibold text-text-primary mb-4">
          Change Callsign
        </h2>

        {user.pending_callsign ? (
          <div className="flex items-center gap-2 text-sm">
            <div className="h-2 w-2 rounded-full bg-warning animate-pulse" />
            <span className="text-text-muted">Pending approval:</span>
            <span className="font-mono text-warning">
              {user.pending_callsign}
            </span>
          </div>
        ) : (
          <form onSubmit={handleCallsignChange} className="flex gap-3">
            <div className="flex-1">
              <Input
                value={newCallsign}
                onChange={(e) => setNewCallsign(e.target.value.toUpperCase())}
                placeholder="W0NEW"
                error={error || undefined}
                mono
              />
            </div>
            <Button type="submit" loading={loading} className="self-start">
              Request Change
            </Button>
          </form>
        )}
      </div>

      {/* PAT placeholder */}
      <div className="bg-bg-surface border border-border rounded-lg p-6">
        <h2 className="text-lg font-semibold text-text-primary mb-2">
          Personal Access Tokens
        </h2>
        <p className="text-text-muted text-sm">
          Token management is coming soon.
        </p>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Create `frontend/src/pages/PlaceholderPage.tsx`**

```tsx
interface PlaceholderPageProps {
  title: string;
}

export function PlaceholderPage({ title }: PlaceholderPageProps) {
  return (
    <div>
      <h1 className="text-xl font-bold text-text-primary mb-4">{title}</h1>
      <div className="bg-bg-surface border border-border rounded-lg p-8 text-center">
        <p className="text-text-muted">This feature is under development.</p>
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Create `frontend/src/pages/NotFoundPage.tsx`**

```tsx
import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="min-h-screen bg-bg-base flex items-center justify-center p-4">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-text-primary mb-2">404</h1>
        <p className="text-text-muted mb-6">Page not found</p>
        <Link
          to="/schedule"
          className="text-accent hover:text-accent-hover transition-colors text-sm"
        >
          Back to schedule
        </Link>
      </div>
    </div>
  );
}
```

- [ ] **Step 8: Verify build**

Run:
```bash
nix-shell --run "cd frontend && npx tsc -b" 2>&1 | tail -10
```

Expected: No type errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/
git commit -m "feat(frontend): add all pages (login, register, pending, schedule, profile, placeholder, 404)"
```

---

### Task 7: Router + App Wiring

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Replace `frontend/src/App.tsx`**

Replace the entire file:

```tsx
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider } from "./context/ThemeContext";
import { AuthProvider } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ToastContainer } from "./components/Toast";
import { AppShell } from "./layouts/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { PendingPage } from "./pages/PendingPage";
import { SchedulePage } from "./pages/SchedulePage";
import { ProfilePage } from "./pages/ProfilePage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProtectedRoute } from "./ProtectedRoute";
import type { UserRole } from "./types";

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route
        path="/register"
        element={
          <ProtectedRoute allowPending pendingOnly>
            <RegisterPage />
          </ProtectedRoute>
        }
      />

      <Route
        path="/pending"
        element={
          <ProtectedRoute allowPending>
            <PendingPage />
          </ProtectedRoute>
        }
      />

      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route path="/schedule" element={<SchedulePage />} />
        <Route
          path="/profile"
          element={
            <ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}>
              <ProfilePage />
            </ProtectedRoute>
          }
        />
        <Route path="/checkins" element={<ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}><PlaceholderPage title="Check-ins" /></ProtectedRoute>} />
        <Route path="/map" element={<ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}><PlaceholderPage title="Map" /></ProtectedRoute>} />
        <Route path="/reminders" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><PlaceholderPage title="Reminders" /></ProtectedRoute>} />
        <Route path="/roster" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><PlaceholderPage title="Roster" /></ProtectedRoute>} />
        <Route path="/activities" element={<ProtectedRoute minRole={["admin"] as UserRole[]}><PlaceholderPage title="Activities" /></ProtectedRoute>} />
        <Route path="/users" element={<ProtectedRoute minRole={["admin"] as UserRole[]}><PlaceholderPage title="Users" /></ProtectedRoute>} />
        <Route path="/config" element={<ProtectedRoute minRole={["admin"] as UserRole[]}><PlaceholderPage title="Config" /></ProtectedRoute>} />
      </Route>

      <Route path="/" element={<Navigate to="/schedule" replace />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
          <ToastProvider>
            <AppRoutes />
            <ToastContainer />
          </ToastProvider>
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
```

- [ ] **Step 2: Create `frontend/src/ProtectedRoute.tsx`**

```tsx
import { Navigate } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import { Spinner } from "./components/Spinner";
import type { UserRole } from "./types";

interface ProtectedRouteProps {
  children: React.ReactNode;
  minRole?: UserRole[];
  allowPending?: boolean;
  pendingOnly?: boolean;
}

export function ProtectedRoute({
  children,
  minRole,
  allowPending = false,
  pendingOnly = false,
}: ProtectedRouteProps) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-bg-base flex items-center justify-center">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  // Handle PENDING users
  if (user.role === "pending") {
    if (pendingOnly && !user.callsign.startsWith("PENDING-")) {
      return <Navigate to="/pending" replace />;
    }
    if (!allowPending) {
      if (user.callsign.startsWith("PENDING-")) {
        return <Navigate to="/register" replace />;
      }
      return <Navigate to="/pending" replace />;
    }
  }

  // Non-pending user trying to access pending-only routes
  if (pendingOnly && user.role !== "pending") {
    return <Navigate to="/schedule" replace />;
  }

  // Role check
  if (minRole && !minRole.includes(user.role)) {
    return <Navigate to="/schedule" replace />;
  }

  return <>{children}</>;
}
```

- [ ] **Step 3: Verify build**

Run:
```bash
nix-shell --run "cd frontend && npx tsc -b && npx vite build" 2>&1 | tail -10
```

Expected: TypeScript compilation and Vite build both succeed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx frontend/src/ProtectedRoute.tsx
git commit -m "feat(frontend): wire up React Router with role-based route protection"
```

---

### Task 8: Verification + Cleanup

**Files:**
- Possibly modify: any file with issues

- [ ] **Step 1: Run full TypeScript check**

Run:
```bash
nix-shell --run "cd frontend && npx tsc -b" 2>&1
```

Expected: Clean — no errors.

- [ ] **Step 2: Run Vite production build**

Run:
```bash
nix-shell --run "cd frontend && npx vite build" 2>&1 | tail -10
```

Expected: Build succeeds, outputs to `frontend/dist/`.

- [ ] **Step 3: Run backend tests to confirm nothing broke**

Run:
```bash
nix-shell --run "python -m pytest tests/ -q" 2>&1 | tail -5
```

Expected: All 300 tests pass.

- [ ] **Step 4: Verify dev server starts**

Run:
```bash
nix-shell --run "cd frontend && npx vite --host 127.0.0.1 --port 5173" &
sleep 3
curl -s -o /dev/null -w "%{http_code}" http://localhost:5173
kill %1 2>/dev/null
```

Expected: HTTP 200.

- [ ] **Step 5: Commit any fixes**

If any fixes were needed:
```bash
git add frontend/
git commit -m "fix(frontend): address build issues"
```

- [ ] **Step 6: Final commit log check**

Run:
```bash
git log --oneline -10
```

Verify all expected commits are present.

---
