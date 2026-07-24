import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Spinner } from "../../components/Spinner";
import { useEvent } from "../../context/EventProvider";
import { useEventPositions } from "../../hooks/useEventPositions";
import { useEventUpdates } from "../../hooks/useEventUpdates";
import { useEventWeather } from "../../hooks/useEventWeather";
import { useRadarFrames } from "../../hooks/useRadarFrames";
import { EventMap } from "./EventMap";

const STATUS_BADGE: Record<string, string> = {
  connected: "bg-success/15 text-success",
  reconnecting: "bg-warning/15 text-warning",
  error: "bg-danger/15 text-danger",
  disabled: "bg-bg-elevated text-text-muted",
};

export function EventMapPage() {
  const { event: snapshot } = useEvent();
  const eventId = snapshot.event.id;
  const { updates } = useEventUpdates(eventId);
  const { stations, aprsStatus, aprsStatusDetail, objects } = useEventPositions(
    eventId,
    true,
  );
  const weatherEnabled = updates?.event.weather_enabled ?? false;
  const weather = useEventWeather(eventId, weatherEnabled);
  const radar = useRadarFrames(weatherEnabled);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [radarFrameIndex, setRadarFrameIndex] = useState(0);
  const [radarFrameCount, setRadarFrameCount] = useState(0);

  // Auto-advance radar frames in the full-page view.
  useEffect(() => {
    if (radarFrameCount === 0) return;
    const id = window.setInterval(() => setRadarFrameIndex((i) => (i + 1) % radarFrameCount), 700);
    return () => window.clearInterval(id);
  }, [radarFrameCount]);

  const toggleHide = useCallback((id: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    }), []);

  if (!updates) {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)] p-2 gap-2">
      <div className="flex items-center gap-3 px-2">
        <Link
          to={`/events/${updates.event.id}`}
          className="text-text-muted hover:text-accent text-sm"
        >
          ← {updates.event.name}
        </Link>
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[aprsStatus] ?? STATUS_BADGE.disabled}`}
          title={aprsStatusDetail}
        >
          APRS {aprsStatus}
        </span>
        {hidden.size > 0 && (
          <details className="text-xs text-text-muted">
            <summary className="cursor-pointer">Hidden ({hidden.size})</summary>
            <div className="absolute z-[1000] bg-bg-surface border border-border rounded-md p-2 mt-1 flex flex-col gap-1">
              {[...hidden].map((id) => (
                <button
                  key={id}
                  onClick={() => toggleHide(id)}
                  className="font-mono text-left hover:text-accent"
                >
                  {id} ✕
                </button>
              ))}
            </div>
          </details>
        )}
      </div>
      <div className="flex-1 min-h-0">
        <EventMap
          stations={stations}
          participants={updates.participants}
          posts={updates.posts}
          objects={objects}
          hidden={hidden}
          onToggleHide={toggleHide}
          weatherEnabled={weatherEnabled}
          alerts={weather.alerts}
          frames={radar.frames}
          radarTileUrl={radar.tileUrl}
          radarFrameIndex={radarFrameIndex}
          onRadarFrameCount={setRadarFrameCount}
        />
      </div>
    </div>
  );
}
