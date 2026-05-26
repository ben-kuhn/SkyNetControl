import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "../api/notifications";
import type { Notification } from "../types";

const POLL_INTERVAL_MS = 60_000;

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, now - then);
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function NotificationBell() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [showRead, setShowRead] = useState(false);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const data = await fetchNotifications(showRead);
      setNotifications(data);
    } catch {
      // swallow — bell is a background feature; toast would be noisy
    }
  }, [showRead]);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const unreadCount = useMemo(
    () => notifications.filter((n) => n.read_at === null).length,
    [notifications],
  );

  const handleClick = async (n: Notification) => {
    try {
      await markNotificationRead(n.id);
    } catch {
      // ignore
    }
    setOpen(false);
    if (n.link_url) {
      navigate(n.link_url);
    }
    load();
  };

  const handleMarkAll = async () => {
    try {
      await markAllNotificationsRead();
    } catch {
      // ignore
    }
    load();
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative p-2 text-text-muted hover:text-text-primary rounded"
        aria-label={`Notifications${unreadCount ? ` (${unreadCount} unread)` : ""}`}
      >
        <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute top-0.5 right-0.5 min-w-[16px] h-4 px-1 text-[0.625rem] font-medium bg-accent text-bg-base rounded-full flex items-center justify-center">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute bottom-full right-0 mb-2 w-80 bg-bg-surface border border-border rounded-lg shadow-lg overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-border">
            <span className="text-sm font-semibold text-text-primary">Notifications</span>
            <button
              onClick={handleMarkAll}
              disabled={unreadCount === 0}
              className="text-xs text-accent hover:underline disabled:opacity-50 disabled:hover:no-underline"
            >
              Mark all read
            </button>
          </div>

          <div className="max-h-96 overflow-auto">
            {notifications.length === 0 ? (
              <p className="px-3 py-6 text-center text-sm text-text-muted">
                No {showRead ? "" : "new "}notifications.
              </p>
            ) : (
              notifications.map((n) => (
                <button
                  key={n.id}
                  onClick={() => handleClick(n)}
                  className={`w-full text-left px-3 py-2 border-b border-border last:border-b-0 hover:bg-bg-elevated/50 ${
                    n.read_at !== null ? "opacity-60" : ""
                  }`}
                >
                  <p className="text-sm text-text-primary">{n.message}</p>
                  <p className="text-xs text-text-muted mt-0.5">{relativeTime(n.created_at)}</p>
                </button>
              ))
            )}
          </div>

          <div className="px-3 py-2 border-t border-border flex justify-end">
            <label className="text-xs text-text-muted flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={showRead}
                onChange={(e) => setShowRead(e.target.checked)}
              />
              Show read
            </label>
          </div>
        </div>
      )}
    </div>
  );
}
