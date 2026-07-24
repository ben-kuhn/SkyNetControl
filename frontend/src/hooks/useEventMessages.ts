import { useCallback, useEffect, useRef, useState } from "react";
import { fetchEventMessages } from "../api/events";
import type { EventMessage } from "../types";

const POLL_MS = 5000;

/**
 * Polls all event Winlink messages on a fixed interval. Always fetches from
 * since=0 so that mutable status fields (read/dismissed) stay in sync;
 * message status changes do not bump msg_seq, so a delta-only approach would
 * miss them. The message set per event is small (dozens), so a full read every
 * 5 s is acceptable. Polls only while `enabled` and the tab is visible.
 */
export function useEventMessages(eventId: number, enabled: boolean) {
  const [messages, setMessages] = useState<EventMessage[]>([]);
  const [latestMsgSeq, setLatestMsgSeq] = useState(0);
  const [messagingConfigured, setMessagingConfigured] = useState(false);
  const byId = useRef<Map<number, EventMessage>>(new Map());

  const refresh = useCallback(async () => {
    try {
      // Always re-read from 0: message status (read/dismissed) is mutable and
      // does not bump msg_seq, so a pure delta would miss status flips. The
      // message set per event is small (dozens), so a full read every 5s is fine.
      const u = await fetchEventMessages(eventId, 0, true);
      const map = new Map<number, EventMessage>();
      for (const m of u.messages) map.set(m.id, m);
      byId.current = map;
      setMessages([...map.values()].sort((a, b) => b.msg_seq - a.msg_seq));
      setLatestMsgSeq(u.latest_msg_seq);
      setMessagingConfigured(u.messaging_configured);
    } catch {
      // keep last-known messages on a failed poll
    }
  }, [eventId]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh, enabled]);

  const unreadCount = messages.filter((m) => m.status === "unread").length;
  return { messages, latestMsgSeq, unreadCount, messagingConfigured, refresh };
}
