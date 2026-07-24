import { useCallback, useEffect, useRef, useState } from "react";
import { fetchPatSession } from "../api/events";
import type { PatSession } from "../types";

const TERMINAL = new Set(["completed", "failed", "aborted"]);
const POLL_MS = 1500;

export function usePatSession(eventId: number, sessionId: number | null) {
  const [session, setSession] = useState<PatSession | null>(null);
  const timer = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (timer.current !== null) { window.clearInterval(timer.current); timer.current = null; }
  }, []);

  useEffect(() => {
    setSession(null);
    if (sessionId == null) { stop(); return; }
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchPatSession(eventId, sessionId);
        if (cancelled) return;
        setSession(s);
        if (TERMINAL.has(s.status)) stop();
      } catch { /* keep last-known on transient poll failure */ }
    };
    void tick();
    timer.current = window.setInterval(() => void tick(), POLL_MS);
    return () => { cancelled = true; stop(); };
  }, [eventId, sessionId, stop]);

  return session;
}
