import { useCallback, useEffect, useRef, useState } from "react";
import { fetchEventUpdates } from "../api/events";
import type { EventLogEntry, EventUpdates } from "../types";

const POLL_MS = 3000;

/**
 * Cursor-polling live updates for an event dashboard.
 *
 * - First poll uses since=0 (full log), then advances the cursor to
 *   latest_seq; log entries accumulate client-side.
 * - Polls every 3s while the event is active and the tab is visible.
 * - On failure keeps last-known state and flips `connected` false; next
 *   successful poll recovers (never blank the dashboard mid-event).
 */
export function useEventUpdates(netSlug: string, eventId: number) {
  const [updates, setUpdates] = useState<EventUpdates | null>(null);
  const [connected, setConnected] = useState(true);
  const sinceRef = useRef(0);
  const logRef = useRef<EventLogEntry[]>([]);
  const statusRef = useRef<string>("active");

  const refresh = useCallback(async () => {
    try {
      const u = await fetchEventUpdates(eventId, sinceRef.current, netSlug);
      logRef.current = [...logRef.current, ...u.log];
      sinceRef.current = u.latest_seq;
      statusRef.current = u.event.status;
      setUpdates({ ...u, log: logRef.current });
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, [netSlug, eventId]);

  useEffect(() => {
    sinceRef.current = 0;
    logRef.current = [];
    statusRef.current = "active";
    setUpdates(null);
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible" && statusRef.current === "active") {
        void refresh();
      }
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  return { updates, connected, refresh };
}
