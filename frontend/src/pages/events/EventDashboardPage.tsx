import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Spinner } from "../../components/Spinner";
import { useCurrentNet } from "../../hooks/useCurrentNet";
import { useEventUpdates } from "../../hooks/useEventUpdates";
import { NetLogPanel } from "./NetLogPanel";
import { ParticipantBoard, STATUS_LABEL } from "./ParticipantBoard";

export function EventDashboardPage() {
  const { slug, eventId } = useParams<{ slug: string; eventId: string }>();
  const { role } = useCurrentNet();
  const canWrite = role === "net_control" || role === "admin";
  const { updates, connected, refresh } = useEventUpdates(slug!, Number(eventId));
  const [selectedId, setSelectedId] = useState<number | null>(null);

  if (!updates) {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }

  const { event, posts, participants, log } = updates;
  const selected = participants.find((p) => p.id === selectedId) ?? null;
  const selectedLog = selected ? log.filter((e) => e.callsign === selected.callsign) : [];
  const pinned = selected ? selectedLog.filter((e) => e.pinned) : [];

  // canWrite/refresh wired up by the NCS-controls task; referenced here so
  // the read-only build stays lint-clean.
  void canWrite;
  void refresh;

  return (
    <div className="p-4 md:p-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <Link to={`/nets/${slug}/events`} className="text-text-muted hover:text-accent text-sm">
          ← Events
        </Link>
        <h1 className="text-xl font-semibold text-text-primary">{event.name}</h1>
        <span className="px-2 py-0.5 rounded text-xs font-medium bg-bg-elevated text-text-secondary">
          {event.event_type === "public_service" ? "Public service" : "Emergency"}
        </span>
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${
            event.status === "active" ? "bg-success/15 text-success" : "bg-bg-elevated text-text-muted"
          }`}
        >
          {event.status}
        </span>
        {!connected && (
          <span className="text-xs text-danger animate-pulse">reconnecting…</span>
        )}
        <div className="ml-auto">
          <Link
            to={`/nets/${slug}/events/${event.id}/report`}
            className="text-sm text-accent hover:underline"
          >
            Report
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Participant board */}
        <div className="lg:col-span-2">
          <ParticipantBoard
            participants={participants}
            posts={posts}
            selectedId={selectedId}
            onSelect={(id) => setSelectedId(id === selectedId ? null : id)}
          />

          {/* Participant detail */}
          {selected && (
            <div className="mt-4 rounded-md border border-border bg-bg-surface p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="font-mono font-semibold text-text-primary">{selected.callsign}</span>
                <span className="text-text-secondary text-sm">{selected.name}</span>
                <span className="text-xs text-text-muted">{STATUS_LABEL[selected.current_status]}</span>
              </div>
              {pinned.length > 0 && (
                <div className="mb-2 flex flex-col gap-1">
                  {pinned.map((e) => (
                    <div key={e.seq} className="text-sm bg-warning/10 rounded px-2 py-1">
                      📌 {e.message}
                    </div>
                  ))}
                </div>
              )}
              <div className="flex flex-col gap-1 max-h-64 overflow-y-auto">
                {[...selectedLog].reverse().map((e) => (
                  <div key={e.seq} className="text-sm text-text-secondary">
                    <span className="text-xs text-text-muted font-mono mr-2">
                      {new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </span>
                    {e.message}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Net log */}
        <div className="min-h-[300px] lg:h-[calc(100vh-12rem)]">
          <NetLogPanel log={log} />
        </div>
      </div>
    </div>
  );
}
