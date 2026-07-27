// frontend/src/pages/events/MapPanel.tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { updateEvent } from "../../api/events";
import type { BeaconedObject, EventParticipant, EventPost, EventStation, NetEvent } from "../../types";
import { useEventWeather } from "../../hooks/useEventWeather";
import { useRadarFrames } from "../../hooks/useRadarFrames";
import { EventMap } from "./EventMap";

const STATUS_BADGE: Record<string, string> = {
  connected: "bg-success/15 text-success",
  reconnecting: "bg-warning/15 text-warning",
  error: "bg-danger/15 text-danger",
  disabled: "bg-bg-elevated text-text-muted",
};

export interface PositionsData {
  stations: Map<string, EventStation>;
  aprsStatus: string;
  aprsStatusDetail: string;
  objects: BeaconedObject[];
}

interface MapPanelProps {
  event: NetEvent;
  participants: EventParticipant[];
  posts: EventPost[];
  canWrite: boolean;
  expanded: boolean;
  onToggleExpanded: () => void;
  positions: PositionsData;
  onEventChanged: () => Promise<void>;
  onError: (message: string) => void;
}

export function MapPanel({
  event,
  participants,
  posts,
  canWrite,
  expanded,
  onToggleExpanded,
  positions,
  onEventChanged,
  onError,
}: MapPanelProps) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [rangeKm, setRangeKm] = useState(event.aprs_range_km?.toString() ?? "50");
  const { stations, aprsStatus, aprsStatusDetail, objects } = positions;
  const weather = useEventWeather(event.id, expanded && event.weather_enabled);
  const radar = useRadarFrames(expanded && event.weather_enabled);
  const [frameCount, setFrameCount] = useState(0);
  const [frameIndex, setFrameIndex] = useState(0);
  const [playing, setPlaying] = useState(true);

  useEffect(() => {
    if (!playing || frameCount === 0) return;
    const id = window.setInterval(() => setFrameIndex((i) => (i + 1) % frameCount), 700);
    return () => window.clearInterval(id);
  }, [playing, frameCount]);

  const toggleHide = (id: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  async function toggleOthers() {
    try {
      if (!event.aprs_other_stations) {
        // Default range center: mean of post coords, else first participant point
        const coords = posts.filter((p) => p.lat != null && p.lon != null);
        const lat =
          event.aprs_range_lat ??
          (coords.length > 0 ? coords.reduce((s, p) => s + (p.lat as number), 0) / coords.length : 39.8283);
        const lon =
          event.aprs_range_lon ??
          (coords.length > 0 ? coords.reduce((s, p) => s + (p.lon as number), 0) / coords.length : -98.5795);
        await updateEvent(
          event.id,
          {
            aprs_other_stations: true,
            aprs_range_lat: lat,
            aprs_range_lon: lon,
            aprs_range_km: Number(rangeKm) || 50,
          },
        );
      } else {
        await updateEvent(event.id, { aprs_other_stations: false });
      }
      await onEventChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Failed to update APRS settings");
    }
  }

  return (
    <div className="rounded-md border border-border bg-bg-surface mb-4">
      <div className="flex items-center gap-3 px-3 py-2">
        <button
          onClick={onToggleExpanded}
          className="text-sm font-semibold text-text-primary hover:text-accent"
        >
          {expanded ? "▾" : "▸"} Live map
        </button>
        {expanded && (
          <span
            className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[aprsStatus] ?? STATUS_BADGE.disabled}`}
            title={aprsStatusDetail}
          >
            APRS {aprsStatus}
          </span>
        )}
        {expanded && event.weather_enabled && (
          <span
            className={`px-2 py-0.5 rounded text-xs font-medium ${
              weather.status === "ok"
                ? "bg-success/15 text-success"
                : weather.status === "stale"
                  ? "bg-warning/15 text-warning"
                  : weather.status === "unavailable"
                    ? "bg-danger/15 text-danger"
                    : "bg-bg-elevated text-text-muted"
            }`}
            title={`NWS alerts: ${weather.status}`}
          >
            Wx {weather.status}
          </span>
        )}
        {expanded && aprsStatus === "disabled" && (
          <Link to={`/events/${event.id}/settings`} className="text-xs text-text-muted hover:text-accent underline">
            enable in event settings
          </Link>
        )}
        {expanded && hidden.size > 0 && (
          <details className="text-xs text-text-muted relative">
            <summary className="cursor-pointer">Hidden ({hidden.size})</summary>
            <div className="absolute z-[1000] bg-bg-surface border border-border rounded-md p-2 mt-1 flex flex-col gap-1">
              {[...hidden].map((id) => (
                <button key={id} onClick={() => toggleHide(id)} className="font-mono text-left hover:text-accent">
                  {id} ✕
                </button>
              ))}
            </div>
          </details>
        )}
        <div className="ml-auto flex items-center gap-3">
          {expanded && canWrite && event.status === "active" && (
            <label className="flex items-center gap-1 text-xs text-text-muted">
              <input type="checkbox" checked={event.aprs_other_stations} onChange={() => void toggleOthers()} />
              Other stations
              {!event.aprs_other_stations && (
                <input
                  value={rangeKm}
                  onChange={(e) => setRangeKm(e.target.value)}
                  className="w-14 rounded bg-bg-elevated border border-border px-1 py-0.5 text-xs"
                  title="Range (km)"
                />
              )}
            </label>
          )}
          <Link to={`/events/${event.id}/map`} className="text-xs text-accent hover:underline">
            Expand
          </Link>
        </div>
      </div>
      {expanded && (
        <div className="border-t border-border">
          <div className="h-[360px]">
            <EventMap
              stations={stations}
              participants={participants}
              posts={posts}
              objects={objects}
              hidden={hidden}
              onToggleHide={toggleHide}
              weatherEnabled={event.weather_enabled}
              alerts={weather.alerts}
              frames={radar.frames}
              radarTileUrl={radar.tileUrl}
              radarFrameIndex={frameIndex}
              onRadarFrameCount={setFrameCount}
            />
          </div>
          {event.weather_enabled && radar.frames.length > 0 && (
            <div className="flex items-center gap-2 text-xs px-3 py-1 border-t border-border">
              <button className="px-2 py-0.5 rounded bg-bg-elevated" onClick={() => setPlaying((p) => !p)}>
                {playing ? "⏸" : "▶"}
              </button>
              <input
                type="range"
                min={0}
                max={frameCount - 1}
                value={frameIndex}
                onChange={(e) => { setPlaying(false); setFrameIndex(Number(e.target.value)); }}
                className="flex-1"
              />
              <span className="text-text-muted tabular-nums">
                {radar.frames[frameIndex] ? new Date(radar.frames[frameIndex].time * 1000).toLocaleTimeString() : ""}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
