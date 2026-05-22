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
        <NavLink
          to="/privacy"
          className="text-xs text-text-muted hover:text-text-secondary transition-colors px-2"
        >
          Privacy Policy
        </NavLink>
      </div>
    </aside>
  );
}
