import { NavLink } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ThemeToggle } from "../components/ThemeToggle";
import { NotificationBell } from "../components/NotificationBell";
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
        {user ? (
          <>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1">
                <NotificationBell />
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
