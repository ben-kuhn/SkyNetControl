import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  activateEvent,
  addEventNote,
  closeEvent,
  reopenEvent,
  setEventLogPinned,
  updateParticipant,
} from "../../api/events";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/ToastContext";
import { useCurrentNet } from "../../hooks/useCurrentNet";
import { useEventMessages } from "../../hooks/useEventMessages";
import { useEventPositions } from "../../hooks/useEventPositions";
import { useEventUpdates } from "../../hooks/useEventUpdates";
import type { ParticipantStatus } from "../../types";
import { CheckInBar } from "./CheckInBar";
import { MapPanel } from "./MapPanel";
import { MessagesPanel } from "./MessagesPanel";
import { NetLogPanel } from "./NetLogPanel";
import { ParticipantBoard, STATUS_LABEL } from "./ParticipantBoard";
import { PostsPanel } from "./PostsPanel";

export function EventDashboardPage() {
  const { slug, eventId } = useParams<{ slug: string; eventId: string }>();
  const { role } = useCurrentNet();
  const canWrite = role === "net_control" || role === "admin";
  const { updates, connected, refresh } = useEventUpdates(slug!, Number(eventId));
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [noteText, setNoteText] = useState("");
  const [participantNote, setParticipantNote] = useState("");
  const [pinNote, setPinNote] = useState(false);
  const [mapExpanded, setMapExpanded] = useState(false);
  const positions = useEventPositions(
    slug!,
    Number(eventId),
    mapExpanded || (updates?.event.aprs_beacon_posts ?? false),
  );
  const [messagesOpen, setMessagesOpen] = useState(false);
  const eventMessages = useEventMessages(
    slug!,
    Number(eventId),
    messagesOpen || (updates?.event.status === "active"),
  );

  // Point 3: toast-driven error reporter and generic action helper
  const { addToast } = useToast();
  const onError = (message: string) => addToast(message, "error");
  const onInfo = (message: string) => addToast(message, "success");

  async function act(fn: () => Promise<unknown>, failMessage: string) {
    try {
      await fn();
      await refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : failMessage);
    }
  }

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
        {/* Point 4: lifecycle buttons */}
        {canWrite && event.status === "draft" && (
          <Button size="sm" onClick={() => void act(() => activateEvent(event.id, slug!), "Activate failed")}>
            Activate
          </Button>
        )}
        {canWrite && event.status === "active" && (
          <Button size="sm" variant="danger" onClick={() => void act(() => closeEvent(event.id, slug!), "Close failed")}>
            Close event
          </Button>
        )}
        {canWrite && event.status === "closed" && (
          <Button size="sm" variant="secondary" onClick={() => void act(() => reopenEvent(event.id, slug!), "Reopen failed")}>
            Reopen
          </Button>
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

      {/* Point 5: Check-in bar above the board */}
      {canWrite && event.status === "active" && (
        <CheckInBar netSlug={slug!} eventId={event.id} posts={posts} onDone={refresh} onError={onError} />
      )}

      <MapPanel
        netSlug={slug!}
        event={event}
        participants={participants}
        posts={posts}
        canWrite={canWrite}
        expanded={mapExpanded}
        onToggleExpanded={() => setMapExpanded(!mapExpanded)}
        positions={positions}
        onEventChanged={refresh}
        onError={onError}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Participant board */}
        <div className="lg:col-span-2">
          {/* Point 6: actions render prop for status change per row */}
          <ParticipantBoard
            participants={participants}
            posts={posts}
            selectedId={selectedId}
            onSelect={(id) => setSelectedId(id === selectedId ? null : id)}
            actions={canWrite && event.status === "active" ? (p) => (
              <select
                value={p.current_status}
                onChange={(e) =>
                  void act(
                    () => updateParticipant(event.id, p.id, { status: e.target.value as ParticipantStatus }, slug!),
                    "Status change failed",
                  )
                }
                className="rounded-md bg-bg-elevated border border-border px-2 py-1 text-xs text-text-primary"
              >
                {(p.current_status === "checked_out"
                  ? ["checked_out", "checked_in"]
                  : ["checked_in", "at_post", "en_route", "out_of_service", "checked_out"]
                ).map((s) => (
                  <option key={s} value={s}>{STATUS_LABEL[s as ParticipantStatus]}</option>
                ))}
              </select>
            ) : undefined}
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
                    <div key={e.seq} className="text-sm bg-warning/10 rounded px-2 py-1 flex items-center gap-1">
                      📌 {e.message}
                      {/* Point 8 unpin control */}
                      {canWrite && event.status === "active" && (
                        <button
                          onClick={() => void act(() => setEventLogPinned(event.id, e.id, false, slug!), "Unpin failed")}
                          className="ml-2 text-xs text-text-muted hover:text-danger"
                        >
                          unpin
                        </button>
                      )}
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
              {/* Point 8: participant note composer */}
              {canWrite && event.status === "active" && (
                <div className="flex gap-2 items-center mt-2">
                  <input
                    value={participantNote}
                    onChange={(e) => setParticipantNote(e.target.value)}
                    placeholder={`Note on ${selected.callsign}…`}
                    className="flex-1 rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
                  />
                  <label className="text-xs text-text-muted flex items-center gap-1">
                    <input type="checkbox" checked={pinNote} onChange={(e) => setPinNote(e.target.checked)} />
                    Pin
                  </label>
                  <Button
                    size="sm"
                    disabled={!participantNote.trim()}
                    onClick={() =>
                      void act(async () => {
                        await addEventNote(
                          event.id,
                          { message: participantNote.trim(), callsign: selected.callsign, pinned: pinNote },
                          slug!,
                        );
                        setParticipantNote("");
                        setPinNote(false);
                      }, "Failed to add note")
                    }
                  >
                    Add note
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* Point 9: posts panel below participant detail */}
          {canWrite && event.status !== "closed" && (
            <div className="mt-4">
              <PostsPanel
                netSlug={slug!}
                eventId={event.id}
                posts={posts}
                onChanged={refresh}
                onError={onError}
                event={event}
                canWrite={canWrite}
                objects={positions.objects}
              />
            </div>
          )}
        </div>

        {/* Net log */}
        <div className="min-h-[300px] lg:h-[calc(100vh-12rem)]">
          {/* Point 7: log composer for NCS */}
          <NetLogPanel
            log={log}
            composer={canWrite && event.status === "active" ? (
              <div className="flex gap-2">
                <input
                  value={noteText}
                  onChange={(e) => setNoteText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && noteText.trim()) {
                      void act(async () => {
                        await addEventNote(event.id, { message: noteText.trim() }, slug!);
                        setNoteText("");
                      }, "Failed to add note");
                    }
                  }}
                  placeholder="Add log entry…"
                  className="flex-1 rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
                />
                <Button
                  size="sm"
                  disabled={!noteText.trim()}
                  onClick={() =>
                    void act(async () => {
                      await addEventNote(event.id, { message: noteText.trim() }, slug!);
                      setNoteText("");
                    }, "Failed to add note")
                  }
                >
                  Log
                </Button>
              </div>
            ) : undefined}
          />
        </div>
      </div>

      {/* Messages panel */}
      <div className="mt-6">
        <button
          onClick={() => setMessagesOpen(!messagesOpen)}
          className="text-sm font-semibold text-text-primary hover:text-accent flex items-center gap-2"
        >
          {messagesOpen ? "▾" : "▸"} Messages
          {eventMessages.unreadCount > 0 && (
            <span className="px-1.5 py-0.5 rounded-full text-xs bg-accent text-bg-base">
              {eventMessages.unreadCount}
            </span>
          )}
        </button>
        {messagesOpen && (
          <div className="mt-2">
            <MessagesPanel
              netSlug={slug!}
              event={event}
              messages={eventMessages.messages}
              messagingConfigured={eventMessages.messagingConfigured}
              canWrite={canWrite}
              onChanged={eventMessages.refresh}
              onError={onError}
              onInfo={onInfo}
            />
          </div>
        )}
      </div>
    </div>
  );
}
