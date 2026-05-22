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
        {user ? (
          <span className="font-mono text-xs text-text-muted">{user.callsign}</span>
        ) : (
          <a href="/login" className="text-xs text-accent hover:underline">Sign in</a>
        )}
      </div>

      {/* Main content */}
      <main className="md:ml-60 p-4 md:p-6">
        <Outlet />
      </main>
    </div>
  );
}
