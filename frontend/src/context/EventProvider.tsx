import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchEvent } from "../api/events";
import { Spinner } from "../components/Spinner";
import type { EventSnapshot } from "../types";

interface EventCtx { event: EventSnapshot; isControl: boolean; reload: () => void; }
const Ctx = createContext<EventCtx | null>(null);

export function useEvent(): EventCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useEvent must be used within EventProvider");
  return v;
}

export function EventProvider({ children }: { children: React.ReactNode }) {
  const { eventId } = useParams();
  const id = Number(eventId);
  const [event, setEvent] = useState<EventSnapshot | null>(null);
  const [error, setError] = useState<number | null>(null);

  const reload = useCallback(() => {
    fetchEvent(id).then((e) => { setEvent(e); setError(null); })
      .catch((err) => setError(err?.status ?? 500));
  }, [id]);

  useEffect(() => { reload(); }, [reload]);

  if (error === 401 || error === 403 || error === 404) {
    return <div className="p-8 text-center text-text-muted">Event not available.</div>;
  }
  if (!event) {
    return (
      <div className="min-h-screen bg-bg-base flex items-center justify-center">
        <Spinner size="lg" />
      </div>
    );
  }
  return <Ctx.Provider value={{ event, isControl: Boolean(event.is_control), reload }}>{children}</Ctx.Provider>;
}
