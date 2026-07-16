import { useCallback, useEffect, useRef, useState } from "react";
import { fetchEventMessages } from "../api/events";
import type { EventMessage } from "../types";

const POLL_MS = 5000;

/**
 * Cursor-polling event Winlink messages. Same accumulate-and-dedupe contract as
 * the log/positions hooks: the server returns rows with msg_seq > since; we
 * accumulate, dedupe by msg_seq, and replace a row when its status changes
 * (status is mutable, so a changed row can re-appear on a later poll with a
 * higher seq — but status changes do NOT bump msg_seq, so we also refetch the
 * full unread/dismissed state by re-reading from since=0 on each mount).
 * Polls only while `enabled` and the tab is visible.
 */
export function useEventMessages(netSlug: string, eventId: number, enabled: boolean) {
  const [messages, setMessages] = useState<EventMessage[]>([]);
  const [latestMsgSeq, setLatestMsgSeq] = useState(0);
  const byId = useRef<Map<number, EventMessage>>(new Map());
  const sinceRef = useRef(0);

  const refresh = useCallback(async () => {
    try {
      // Always re-read from 0: message status (read/dismissed) is mutable and
      // does not bump msg_seq, so a pure delta would miss status flips. The
      // message set per event is small (dozens), so a full read every 5s is fine.
      const u = await fetchEventMessages(eventId, 0, netSlug, true);
      const map = new Map<number, EventMessage>();
      for (const m of u.messages) map.set(m.id, m);
      byId.current = map;
      sinceRef.current = u.latest_msg_seq;
      setMessages([...map.values()].sort((a, b) => b.msg_seq - a.msg_seq));
      setLatestMsgSeq(u.latest_msg_seq);
    } catch {
      // keep last-known messages on a failed poll
    }
  }, [netSlug, eventId]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh, enabled]);

  const unreadCount = messages.filter((m) => m.status === "unread").length;
  return { messages, latestMsgSeq, unreadCount, refresh };
}
