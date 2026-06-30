import { NavLink, useParams } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ThemeToggle } from "../components/ThemeToggle";
import { NotificationBell } from "../components/NotificationBell";
import { VersionLabel } from "../components/VersionLabel";
import type { NetRole } from "../types";

interface NavItem {
  label: string;
  subpath: string | null;
  absolutePath?: string;
  minRole: NetRole | "admin" | null;
}

const netNavItems: NavItem[] = [
  { label: "Schedule",   subpath: "schedule",   minRole: "viewer" },
  { label: "Check-ins",  subpath: "checkins",   minRole: null },
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
  if (required === null) return true;
  if (userRole === null) return false;
  return ROLE_RANK[userRole] >= ROLE_RANK[required];
}

interface MobileMenuProps {
  open: boolean;
  onClose: () => void;
}

function readLastNetSlug(): string | undefined {
  if (typeof window === "undefined") return undefined;
  return localStorage.getItem("lastNetSlug") ?? undefined;
}

export function MobileMenu({ open, onClose }: MobileMenuProps) {
  const { user, logout } = useAuth();
  const { slug: urlSlug } = useParams<{ slug?: string }>();
  // Same fallback as Sidebar: when on a global page, keep the per-net nav
  // pointed at whichever net the user last visited.
  const slug = urlSlug ?? readLastNetSlug();

  if (!open) return null;

  const netRole: NetRole | "admin" | null = user?.is_admin
    ? "admin"
    : (user?.nets.find((n) => n.slug === slug)?.role ?? null);

  const visibleNetItems = slug
    ? netNavItems.filter((item) => meetsRole(netRole, item.minRole))
    : [];

  const visibleGlobalItems = globalNavItems.filter((item) =>
    meetsRole(netRole, item.minRole),
  );

  const allItems = [
    ...visibleNetItems.map((item) => ({
      label: item.label,
      to: `/nets/${slug}/${item.subpath}`,
    })),
    ...visibleGlobalItems.map((item) => ({
      label: item.label,
      to: item.absolutePath!,
    })),
  ];

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
        {allItems.map((item) => (
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
        {user ? (
          <>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1">
                <NotificationBell slug={slug} />
                <NavLink
                  to="/profile"
                  onClick={onClose}
                  className="font-mono text-sm text-text-secondary hover:text-accent"
                >
                  {user.callsign}
                </NavLink>
              </div>
              <ThemeToggle />
            </div>
            <VersionLabel />
            <button
              onClick={() => { logout(); onClose(); }}
              className="w-full text-left px-2 py-2 text-sm text-text-muted hover:text-danger transition-colors rounded-md hover:bg-bg-elevated"
            >
              Logout
            </button>
          </>
        ) : (
          <a
            href="/login"
            className="block px-2 py-2 text-sm text-accent hover:underline"
          >
            Sign in
          </a>
        )}
        <NavLink
          to="/privacy"
          onClick={onClose}
          className="text-xs text-text-muted hover:text-text-secondary transition-colors px-2"
        >
          Privacy Policy
        </NavLink>
      </div>
    </div>
  );
}
