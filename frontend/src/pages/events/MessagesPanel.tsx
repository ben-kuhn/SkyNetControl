// frontend/src/pages/events/MessagesPanel.tsx
import { useMemo, useState } from "react";
import { retryDelivery } from "../../api/delivery";
import { rescanEventMailbox, setEventMessageStatus } from "../../api/events";
import { Button } from "../../components/Button";
import type { EventMessage, NetEvent } from "../../types";
import { MessageComposer } from "./MessageComposer";

type Filter = "all" | "unread" | "inbound" | "outbound";

interface MessagesPanelProps {
  netSlug: string;
  event: NetEvent;
  messages: EventMessage[];
  messagingConfigured: boolean;
  canWrite: boolean;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
  onInfo?: (message: string) => void;
}

export function MessagesPanel({
  netSlug,
  event,
  messages,
  messagingConfigured,
  canWrite,
  onChanged,
  onError,
  onInfo,
}: MessagesPanelProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const [includeDismissed, setIncludeDismissed] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [replyTo, setReplyTo] = useState<EventMessage | null>(null);
  const [composeOpen, setComposeOpen] = useState(false);
  const [rescanning, setRescanning] = useState(false);

  const visible = useMemo(() => {
    return messages.filter((m) => {
      if (!includeDismissed && m.status === "dismissed") return false;
      if (filter === "unread") return m.status === "unread";
      if (filter === "inbound") return m.direction === "inbound";
      if (filter === "outbound") return m.direction === "outbound";
      return true;
    });
  }, [messages, filter, includeDismissed]);

  const active = event.status === "active";

  async function handleOpen(m: EventMessage) {
    setOpenId(openId === m.id ? null : m.id);
    if (m.status === "unread" && m.direction === "inbound" && canWrite && active) {
      try {
        await setEventMessageStatus(event.id, m.id, "read", netSlug);
        await onChanged();
      } catch { /* non-fatal */ }
    }
  }

  async function dismiss(m: EventMessage) {
    try {
      await setEventMessageStatus(event.id, m.id, "dismissed", netSlug);
      await onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Dismiss failed");
    }
  }

  async function rescan() {
    setRescanning(true);
    try {
      const { new_messages } = await rescanEventMailbox(event.id, netSlug);
      await onChanged();
      const msg = new_messages > 0 ? `${new_messages} new message(s)` : "No new mail";
      (onInfo ?? onError)(msg);
    } catch (e) {
      onError(e instanceof Error ? e.message : "Re-scan failed");
    } finally {
      setRescanning(false);
    }
  }

  async function retry(m: EventMessage) {
    try {
      await retryDelivery("event_message", m.id, netSlug);
      await onChanged();
      (onInfo ?? onError)("Retry attempted");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Retry failed");
    }
  }

  return (
    <div className="rounded-md border border-border bg-bg-surface p-3">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <h2 className="text-sm font-semibold text-text-primary">Messages</h2>
        <div className="flex gap-1">
          {(["all", "unread", "inbound", "outbound"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2 py-0.5 rounded text-xs ${filter === f ? "bg-accent/15 text-accent" : "text-text-muted hover:text-text-primary"}`}
            >
              {f}
            </button>
          ))}
        </div>
        <label className="text-xs text-text-muted flex items-center gap-1 ml-2">
          <input type="checkbox" checked={includeDismissed} onChange={(e) => setIncludeDismissed(e.target.checked)} />
          dismissed
        </label>
        {canWrite && active && (
          <div className="ml-auto flex gap-2">
            <Button size="sm" variant="secondary" loading={rescanning} onClick={() => void rescan()}>
              Check mail now
            </Button>
            <Button size="sm" onClick={() => { setReplyTo(null); setComposeOpen(true); }}>
              New message
            </Button>
          </div>
        )}
      </div>

      {!messagingConfigured && (
        <p className="text-xs text-text-muted mb-2">
          No PAT mailbox configured for this net — inbound Winlink is off.{" "}
          <a href={`/nets/${netSlug}/settings`} className="text-accent hover:underline">
            Configure it in net settings.
          </a>
        </p>
      )}

      {visible.length === 0 ? (
        <p className="text-text-muted text-sm">No messages.</p>
      ) : (
        <div className="flex flex-col gap-1">
          {visible.map((m) => (
            <div key={m.id} className="border-b border-border pb-1">
              <div
                onClick={() => void handleOpen(m)}
                className="flex items-center gap-2 cursor-pointer py-1 text-sm"
              >
                {m.status === "unread" && m.direction === "inbound" && (
                  <span className="h-2 w-2 rounded-full bg-accent shrink-0" />
                )}
                <span className="font-mono text-text-primary">
                  {m.direction === "inbound" ? m.from_callsign : `→ ${m.to_address}`}
                </span>
                <span className="text-text-secondary truncate flex-1">{m.subject || "(no subject)"}</span>
                <span className="text-xs text-text-muted shrink-0">
                  {new Date(m.received_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
              {openId === m.id && (
                <div className="pl-4 pb-2 text-sm">
                  <pre className="whitespace-pre-wrap font-sans text-text-secondary">{m.body}</pre>
                  {canWrite && active && (
                    <div className="flex gap-2 mt-2">
                      {m.direction === "inbound" && (
                        <button
                          onClick={() => { setReplyTo(m); setComposeOpen(true); }}
                          className="text-xs text-accent hover:underline"
                        >
                          Reply
                        </button>
                      )}
                      {m.direction === "outbound" && (
                        <button onClick={() => void retry(m)} className="text-xs text-text-muted hover:text-accent">
                          Retry send
                        </button>
                      )}
                      {m.status !== "dismissed" && (
                        <button onClick={() => void dismiss(m)} className="text-xs text-text-muted hover:text-danger">
                          Dismiss
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <MessageComposer
        key={replyTo?.id ?? "new"}
        netSlug={netSlug}
        eventId={event.id}
        open={composeOpen}
        onClose={() => setComposeOpen(false)}
        replyTo={replyTo}
        onSent={onChanged}
        onError={onError}
      />
    </div>
  );
}
