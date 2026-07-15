// frontend/src/pages/events/CheckInBar.tsx
import { useRef, useState } from "react";
import { checkInParticipant } from "../../api/events";
import { Button } from "../../components/Button";
import type { EventPost } from "../../types";

interface CheckInBarProps {
  netSlug: string;
  eventId: number;
  posts: EventPost[];
  onDone: () => Promise<void>;
  onError: (message: string) => void;
}

/** Keyboard-first check-in: callsign autofocused, Enter submits, focus
 *  returns to the callsign field for the next check-in. */
export function CheckInBar({ netSlug, eventId, posts, onDone, onError }: CheckInBarProps) {
  const [callsign, setCallsign] = useState("");
  const [postId, setPostId] = useState<number | "">("");
  const [location, setLocation] = useState("");
  const [busy, setBusy] = useState(false);
  const callsignRef = useRef<HTMLInputElement>(null);

  async function submit() {
    const cs = callsign.trim().toUpperCase();
    if (!cs || busy) return;
    setBusy(true);
    try {
      await checkInParticipant(
        eventId,
        { callsign: cs, post_id: postId === "" ? null : postId, location: location.trim() || null },
        netSlug,
      );
      setCallsign("");
      setLocation("");
      await onDone();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Check-in failed");
    } finally {
      setBusy(false);
      callsignRef.current?.focus();
    }
  }

  return (
    <div className="flex gap-2 items-end flex-wrap rounded-md border border-border bg-bg-surface p-3 mb-4">
      <label className="text-sm text-text-secondary">
        Callsign
        <input
          ref={callsignRef}
          autoFocus
          value={callsign}
          onChange={(e) => setCallsign(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
          className="mt-1 block w-36 rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm font-mono text-text-primary"
        />
      </label>
      {posts.length > 0 && (
        <label className="text-sm text-text-secondary">
          Post
          <select
            value={postId}
            onChange={(e) => setPostId(e.target.value === "" ? "" : Number(e.target.value))}
            className="mt-1 block w-44 rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
          >
            <option value="">—</option>
            {posts.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
      )}
      <label className="text-sm text-text-secondary flex-1 min-w-40">
        Location
        <input
          value={location}
          onChange={(e) => setLocation(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
          className="mt-1 block w-full rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
        />
      </label>
      <Button loading={busy} onClick={() => void submit()}>Check in</Button>
    </div>
  );
}
