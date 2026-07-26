import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchEventByToken, fetchEventPositions, fetchEventUpdates, fetchEventWeather } from "../../api/events";
import { useRadarFrames } from "../../hooks/useRadarFrames";
import type { BeaconedObject, EventLogEntry, EventParticipant, EventPost, EventStation, NetEvent, WeatherData } from "../../types";
import { EventMap } from "./EventMap";
import { NetLogPanel } from "./NetLogPanel";

const POLL_UPDATES_MS = 3000;
const POLL_POSITIONS_MS = 5000;
const POLL_WEATHER_MS = 60000;
const EMPTY_WEATHER: WeatherData = {
  alerts: { type: "FeatureCollection", features: [] },
  updated_at: null,
  status: "disabled",
};

export function PublicEventPage() {
  const { token } = useParams<{ token: string }>();

  // Bootstrap state
  const [event, setEvent] = useState<NetEvent | null>(null);
  const [notFound, setNotFound] = useState(false);

  // Live data
  const [log, setLog] = useState<EventLogEntry[]>([]);
  const [participants, setParticipants] = useState<EventParticipant[]>([]);
  const [posts, setPosts] = useState<EventPost[]>([]);
  const [stations, setStations] = useState<Map<string, EventStation>>(new Map());
  const [aprsStatus, setAprsStatus] = useState("disabled");
  const [aprsStatusDetail, setAprsStatusDetail] = useState("");
  const [objects, setObjects] = useState<BeaconedObject[]>([]);
  const [weather, setWeather] = useState<WeatherData>(EMPTY_WEATHER);

  // Cursor refs (stable across renders, no re-subscribe needed)
  const updatesSinceRef = useRef(0);
  const logRef = useRef<EventLogEntry[]>([]);
  const statusRef = useRef<string>("active");
  const positionsSinceRef = useRef(0);
  const positionsPointsRef = useRef<Map<string, EventStation>>(new Map());

  // Bootstrap: fetch event by token
  useEffect(() => {
    if (!token) { setNotFound(true); return; }
    fetchEventByToken(token)
      .then((ev) => setEvent(ev))
      .catch(() => setNotFound(true));
  }, [token]);

  // Poll updates once event is known
  useEffect(() => {
    if (!event || !token) return;
    const eventId = event.id;

    const pollUpdates = async () => {
      try {
        const u = await fetchEventUpdates(eventId, updatesSinceRef.current, token);
        const lastSeq = logRef.current.length > 0 ? logRef.current[logRef.current.length - 1]!.seq : 0;
        logRef.current = [...logRef.current, ...u.log.filter((e) => e.seq > lastSeq)];
        const pinnedSet = new Set(u.pinned_seqs);
        logRef.current = logRef.current.map((e) =>
          pinnedSet.has(e.seq) === e.pinned ? e : { ...e, pinned: pinnedSet.has(e.seq) },
        );
        updatesSinceRef.current = Math.max(updatesSinceRef.current, u.latest_seq);
        statusRef.current = u.event.status;
        setLog([...logRef.current]);
        setParticipants(u.participants);
        setPosts(u.posts);
      } catch {
        // Keep last-known on failed poll (never blank mid-event)
      }
    };

    // Reset cursors when event changes
    updatesSinceRef.current = 0;
    logRef.current = [];
    statusRef.current = "active";
    setLog([]);

    void pollUpdates();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible" && statusRef.current === "active") {
        void pollUpdates();
      }
    }, POLL_UPDATES_MS);
    return () => window.clearInterval(id);
  }, [event, token]);

  // Poll positions
  useEffect(() => {
    if (!event || !token) return;
    const eventId = event.id;

    const pollPositions = async () => {
      try {
        const u = await fetchEventPositions(eventId, positionsSinceRef.current, token);
        const next = new Map<string, EventStation>();
        for (const station of u.stations) {
          const prev = positionsPointsRef.current.get(station.station_id);
          const prevPoints = prev ? prev.points : [];
          const lastPoint = prevPoints[prevPoints.length - 1];
          const lastSeq = lastPoint ? lastPoint.pos_seq : 0;
          next.set(station.station_id, {
            ...station,
            points: [...prevPoints, ...station.points.filter((p) => p.pos_seq > lastSeq)],
          });
        }
        positionsPointsRef.current = next;
        positionsSinceRef.current = Math.max(positionsSinceRef.current, u.latest_pos_seq);
        setStations(next);
        setAprsStatus(u.aprs_status);
        setAprsStatusDetail(u.aprs_status_detail);
        setObjects(u.objects);
      } catch {
        // Keep last-known positions on failed poll
      }
    };

    positionsSinceRef.current = 0;
    positionsPointsRef.current = new Map();
    setStations(new Map());

    void pollPositions();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void pollPositions();
    }, POLL_POSITIONS_MS);
    return () => window.clearInterval(id);
  }, [event, token]);

  // Poll weather
  useEffect(() => {
    if (!event || !token) return;
    if (!event.weather_enabled) { setWeather(EMPTY_WEATHER); return; }
    const eventId = event.id;

    const pollWeather = async () => {
      try {
        setWeather(await fetchEventWeather(eventId, token));
      } catch {
        // Keep last-known weather on failed poll
      }
    };

    void pollWeather();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void pollWeather();
    }, POLL_WEATHER_MS);
    return () => window.clearInterval(id);
  }, [event, token]);

  // Radar frames for weather overlay
  const [mapExpanded, setMapExpanded] = useState(true);
  const radar = useRadarFrames(mapExpanded && (event?.weather_enabled ?? false));
  const [frameCount, setFrameCount] = useState(0);
  const [frameIndex, setFrameIndex] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

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

  // --- Render states ---

  if (notFound) {
    return (
      <div className="min-h-screen grid place-items-center bg-slate-950 text-slate-400">
        <div className="text-center">
          <p className="text-lg font-semibold text-slate-300">Event not found</p>
          <p className="text-sm mt-1">This link may have expired or been revoked.</p>
        </div>
      </div>
    );
  }

  if (!event) {
    return (
      <div className="min-h-screen grid place-items-center bg-slate-950 text-slate-400">
        <p className="text-sm">Loading…</p>
      </div>
    );
  }

  const STATUS_BADGE: Record<string, string> = {
    connected: "bg-success/15 text-success",
    reconnecting: "bg-warning/15 text-warning",
    error: "bg-danger/15 text-danger",
    disabled: "bg-bg-elevated text-text-muted",
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Minimal header — no AppShell, no sidebar */}
      <header className="px-4 py-3 border-b border-slate-800 flex items-center gap-3 flex-wrap">
        <h1 className="text-lg font-semibold text-slate-100">{event.name}</h1>
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${
            event.status === "active"
              ? "bg-green-900/40 text-green-400"
              : "bg-slate-800 text-slate-400"
          }`}
        >
          {event.status}
        </span>
        {event.weather_enabled && (
          <span
            className={`px-2 py-0.5 rounded text-xs font-medium ${
              weather.status === "ok"
                ? "bg-green-900/40 text-green-400"
                : weather.status === "stale"
                  ? "bg-yellow-900/40 text-yellow-400"
                  : weather.status === "unavailable"
                    ? "bg-red-900/40 text-red-400"
                    : "bg-slate-800 text-slate-400"
            }`}
          >
            Wx {weather.status}
          </span>
        )}
      </header>

      <div className="p-4 md:p-6">
        {/* Live map */}
        <div className="rounded-md border border-slate-800 bg-slate-900 mb-4">
          <div className="flex items-center gap-3 px-3 py-2">
            <button
              onClick={() => setMapExpanded(!mapExpanded)}
              className="text-sm font-semibold text-slate-200 hover:text-blue-400"
            >
              {mapExpanded ? "▾" : "▸"} Live map
            </button>
            {mapExpanded && (
              <span
                className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[aprsStatus] ?? STATUS_BADGE.disabled}`}
                title={aprsStatusDetail}
              >
                APRS {aprsStatus}
              </span>
            )}
          </div>
          {mapExpanded && (
            <div className="border-t border-slate-800">
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
                <div className="flex items-center gap-2 text-xs px-3 py-1 border-t border-slate-800">
                  <button
                    className="px-2 py-0.5 rounded bg-slate-800"
                    onClick={() => setPlaying((p) => !p)}
                  >
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
                  <span className="text-slate-400 tabular-nums">
                    {radar.frames[frameIndex]
                      ? new Date(radar.frames[frameIndex].time * 1000).toLocaleTimeString()
                      : ""}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Net log — read-only, no composer */}
        <div className="rounded-md border border-slate-800 bg-slate-900 p-4 min-h-[300px] max-h-[60vh] overflow-y-auto">
          <NetLogPanel log={log} />
        </div>
      </div>
    </div>
  );
}
