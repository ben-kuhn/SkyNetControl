import { NavLink, useParams } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ThemeToggle } from "../components/ThemeToggle";
import { NotificationBell } from "../components/NotificationBell";
import { VersionLabel } from "../components/VersionLabel";
import { NetPicker } from "../components/NetPicker";
import type { NetRole } from "../types";

interface NavItem {
  label: string;
  /** Relative to the current net slug, or an absolute path for global pages. */
  subpath: string | null;
  /** Absolute path for global (non-net-scoped) pages. */
  absolutePath?: string;
  /** Minimum NetRole required, or "admin" for global admin pages, or null for public. */
  minRole: NetRole | "admin" | null;
}

const netNavItems: NavItem[] = [
  { label: "Schedule",   subpath: "schedule",   minRole: "viewer" },
  { label: "Check-ins",  subpath: "checkins",   minRole: null },  // public
  { label: "Members",    subpath: "members",    minRole: "viewer" },
  { label: "Reminders",  subpath: "reminders",  minRole: "net_control" },
  { label: "Roster",     subpath: "roster",     minRole: "net_control" },
  { label: "Activities", subpath: "activities", minRole: "net_control" },
  { label: "Settings",   subpath: "settings",   minRole: "net_control" },
];

const globalNavItems: NavItem[] = [
  { label: "Users",  subpath: null, absolutePath: "/users",  minRole: "admin" },
  { label: "Config", subpath: null, absolutePath: "/config", minRole: "admin" },
  { label: "Nets",   subpath: null, absolutePath: "/nets",   minRole: "admin" },
];

const ROLE_RANK: Record<NetRole | "admin", number> = {
  viewer: 1,
  net_control: 2,
  admin: 99,
};

function meetsRole(
  userRole: NetRole | "admin" | null,
  required: NetRole | "admin" | null,
): boolean {
  if (required === null) return true; // public
  if (userRole === null) return false;
  return ROLE_RANK[userRole] >= ROLE_RANK[required];
}

export function Sidebar() {
  const { user, logout } = useAuth();
  const { slug } = useParams<{ slug?: string }>();

  // Determine effective role for nav filtering
  const netRole: NetRole | "admin" | null = user?.is_admin
    ? "admin"
    : (user?.nets.find((n) => n.slug === slug)?.role ?? null);

  const visibleNetItems = slug
    ? netNavItems.filter((item) => meetsRole(netRole, item.minRole))
    : [];

  const visibleGlobalItems = globalNavItems.filter((item) =>
    meetsRole(netRole, item.minRole),
  );

  return (
    <aside className="hidden md:flex md:flex-col md:w-60 md:fixed md:inset-y-0 bg-bg-surface border-r border-border">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-4 border-b border-border bg-linear-to-b from-bg-elevated to-bg-surface">
        <div className="h-2 w-2 rounded-full bg-success shadow-[0_0_6px_rgba(34,197,94,0.5)]" />
        <span className="font-bold text-accent tracking-wide">SkyNetControl</span>
      </div>

      {/* Net picker (when inside a net) */}
      {slug && (
        <div className="px-3 pt-3 pb-1">
          <NetPicker />
        </div>
      )}

      {/* Nav links */}
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {visibleNetItems.map((item) => (
          <NavLink
            key={item.subpath}
            to={`/nets/${slug}/${item.subpath}`}
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

        {/* Divider before global admin links (when both sections have items) */}
        {visibleNetItems.length > 0 && visibleGlobalItems.length > 0 && (
          <div className="my-2 border-t border-border" />
        )}

        {visibleGlobalItems.map((item) => (
          <NavLink
            key={item.absolutePath}
            to={item.absolutePath!}
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
      {user ? (
        <div className="border-t border-border px-3 py-3 flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <ThemeToggle />
            <div className="flex items-center gap-1">
              <NotificationBell />
              <NavLink
                to="/profile"
                className="font-mono text-sm text-text-secondary hover:text-accent transition-colors"
              >
                {user.callsign}
              </NavLink>
            </div>
          </div>
          <VersionLabel />
          <button
            onClick={logout}
            className="w-full text-left px-2 py-1.5 text-sm text-text-muted hover:text-danger transition-colors rounded-md hover:bg-bg-elevated"
          >
            Logout
          </button>
          <NavLink
            to="/privacy"
            className="text-xs text-text-muted hover:text-text-secondary transition-colors px-2"
          >
            Privacy Policy
          </NavLink>
        </div>
      ) : (
        <a
          href="/login"
          className="block px-4 py-2 text-sm text-accent hover:underline"
        >
          Sign in
        </a>
      )}
    </aside>
  );
}
