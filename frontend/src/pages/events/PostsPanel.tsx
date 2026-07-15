// frontend/src/pages/events/PostsPanel.tsx
import { useState } from "react";
import { createEventPost, deleteEventPost } from "../../api/events";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import type { EventPost } from "../../types";

interface PostsPanelProps {
  netSlug: string;
  eventId: number;
  posts: EventPost[];
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
}

export function PostsPanel({ netSlug, eventId, posts, onChanged, onError }: PostsPanelProps) {
  const [name, setName] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [busy, setBusy] = useState(false);

  async function add() {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      await createEventPost(
        eventId,
        {
          name: name.trim(),
          lat: lat.trim() ? Number(lat) : null,
          lon: lon.trim() ? Number(lon) : null,
        },
        netSlug,
      );
      setName("");
      setLat("");
      setLon("");
      await onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Failed to add post");
    } finally {
      setBusy(false);
    }
  }

  async function remove(postId: number) {
    try {
      await deleteEventPost(eventId, postId, netSlug);
      await onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Failed to delete post (still assigned?)");
    }
  }

  return (
    <div className="rounded-md border border-border bg-bg-surface p-3">
      <h3 className="text-sm font-semibold text-text-primary mb-2">Posts</h3>
      {posts.length > 0 && (
        <ul className="mb-3 flex flex-col gap-1">
          {posts.map((p) => (
            <li key={p.id} className="flex items-center justify-between text-sm">
              <span className="text-text-secondary">
                {p.name}
                {p.lat != null && p.lon != null && (
                  <span className="text-xs text-text-muted ml-2">({p.lat}, {p.lon})</span>
                )}
              </span>
              <button
                onClick={() => void remove(p.id)}
                className="text-xs text-text-muted hover:text-danger"
              >
                remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2 items-end flex-wrap">
        <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <Input label="Lat" value={lat} onChange={(e) => setLat(e.target.value)} className="w-24" />
        <Input label="Lon" value={lon} onChange={(e) => setLon(e.target.value)} className="w-24" />
        <Button size="sm" loading={busy} onClick={() => void add()}>Add post</Button>
      </div>
    </div>
  );
}
