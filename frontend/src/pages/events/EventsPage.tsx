import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import { createEvent, fetchEvents } from "../../api/events";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { Spinner } from "../../components/Spinner";
import type { EventStatus, EventType, NetEvent } from "../../types";

const STATUS_BADGE: Record<EventStatus, string> = {
  draft: "bg-bg-elevated text-text-muted",
  active: "bg-success/15 text-success",
  closed: "bg-bg-elevated text-text-secondary",
};

const TYPE_LABEL: Record<EventType, string> = {
  public_service: "Public service",
  emergency: "Emergency",
};

export function EventsPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [events, setEvents] = useState<NetEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(searchParams.get("new") === "1");

  // Create form state
  const [name, setName] = useState("");
  const [eventType, setEventType] = useState<EventType>("public_service");
  const [description, setDescription] = useState("");
  const [scheduledStart, setScheduledStart] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setEvents(await fetchEvents());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load events");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit() {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const created = await createEvent({
        name: name.trim(),
        event_type: eventType,
        description: description.trim() || null,
        scheduled_start: scheduledStart ? new Date(scheduledStart).toISOString() : null,
      });
      navigate(`/events/${created.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create event");
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-5xl">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text-primary">Events</h1>
        <Button onClick={() => setShowCreate(true)}>New event</Button>
      </div>

      {error && <p className="text-danger text-sm mb-3">{error}</p>}

      {events.length === 0 ? (
        <p className="text-text-muted text-sm">No events yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-text-muted border-b border-border">
              <th className="py-2 pr-4">Name</th>
              <th className="py-2 pr-4">Type</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2 pr-4">Scheduled</th>
              <th className="py-2">Activated</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id} className="border-b border-border hover:bg-bg-elevated">
                <td className="py-2 pr-4">
                  <Link to={`/events/${event.id}`} className="text-accent hover:underline">
                    {event.name}
                  </Link>
                </td>
                <td className="py-2 pr-4 text-text-secondary">{TYPE_LABEL[event.event_type]}</td>
                <td className="py-2 pr-4">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[event.status]}`}>
                    {event.status}
                  </span>
                </td>
                <td className="py-2 pr-4 text-text-muted">
                  {event.scheduled_start ? new Date(event.scheduled_start).toLocaleString() : "—"}
                </td>
                <td className="py-2 text-text-muted">
                  {event.activated_at ? new Date(event.activated_at).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="New event">
        <div className="flex flex-col gap-3">
          <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <label className="text-sm text-text-secondary">
            Type
            <select
              value={eventType}
              onChange={(e) => setEventType(e.target.value as EventType)}
              className="mt-1 block w-full rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
            >
              <option value="public_service">Public service</option>
              <option value="emergency">Emergency</option>
            </select>
          </label>
          <Input label="Description" value={description} onChange={(e) => setDescription(e.target.value)} />
          <Input
            label="Scheduled start"
            type="datetime-local"
            value={scheduledStart}
            onChange={(e) => setScheduledStart(e.target.value)}
          />
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button loading={saving} onClick={() => void submit()}>
              Create draft
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
