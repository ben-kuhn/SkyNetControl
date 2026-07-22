import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useTheme } from "../../hooks/useTheme";
import type { BeaconedObject, EventParticipant, EventPost, EventStation, ParticipantStatus, WeatherAlerts, WeatherFeature } from "../../types";

const TILE_URL_DARK = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_URL_LIGHT = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/">CARTO</a>';
const DEFAULT_CENTER: L.LatLngExpression = [39.8283, -98.5795];
const DEFAULT_ZOOM = 4;
const STALE_MS = 15 * 60 * 1000; // dim markers not heard for 15 min

const STATUS_COLOR: Record<ParticipantStatus, string> = {
  checked_in: "#22c55e",
  at_post: "#22d3ee",
  en_route: "#fbbf24",
  out_of_service: "#ef4444",
  checked_out: "#71717a",
};
const OTHER_COLOR = "#9ca3af";
const POST_COLOR = "#a78bfa";

function alertStyle(feature?: WeatherFeature): L.PathOptions {
  const ev = (feature?.properties?.event ?? "").toLowerCase();
  const sev = (feature?.properties?.severity ?? "").toLowerCase();
  const isWarning = ev.includes("warning");
  let color = "#9ca3af"; // muted default (advisories/statements)
  if (ev.includes("tornado") && isWarning) color = "#dc2626"; // red
  else if (isWarning && (sev === "extreme" || sev === "severe")) color = "#ef4444";
  else if (ev.includes("watch")) color = "#f59e0b"; // amber
  return { color, weight: 2, fillColor: color, fillOpacity: 0.15 };
}

export interface EventMapProps {
  stations: Map<string, EventStation>;
  participants: EventParticipant[];
  posts: EventPost[];
  objects: BeaconedObject[];
  hidden: Set<string>;
  onToggleHide: (stationId: string) => void;
  weatherEnabled: boolean;
  alerts: WeatherAlerts;
}

function popupContent(title: string, lines: string[], hideId: string | null, onHide: (id: string) => void) {
  const el = document.createElement("div");
  const strong = document.createElement("strong");
  strong.style.fontFamily = "monospace";
  strong.textContent = title;
  el.appendChild(strong);
  for (const line of lines) {
    el.appendChild(document.createElement("br"));
    el.appendChild(document.createTextNode(line));
  }
  if (hideId !== null) {
    const btn = document.createElement("button");
    btn.textContent = "hide";
    btn.style.cssText = "display:block;margin-top:4px;font-size:11px;text-decoration:underline;cursor:pointer";
    btn.onclick = () => onHide(hideId);
    el.appendChild(btn);
  }
  return el;
}

export function EventMap({ stations, participants, posts, objects, hidden, onToggleHide, weatherEnabled, alerts }: EventMapProps) {
  const { theme } = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tileLayerRef = useRef<L.TileLayer | null>(null);
  const layersRef = useRef<{
    participants: L.LayerGroup;
    trails: L.LayerGroup;
    posts: L.LayerGroup;
    others: L.LayerGroup;
    weather: L.LayerGroup;
  } | null>(null);
  const fittedRef = useRef(false);
  const markersRef = useRef<Map<string, L.CircleMarker>>(new Map());
  const openPopupRef = useRef<string | null>(null);

  // Init once (same conventions as CheckInMap: invalidateSize on settle/resize)
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const container = containerRef.current;
    const map = L.map(container, { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM });
    tileLayerRef.current = L.tileLayer(theme === "light" ? TILE_URL_LIGHT : TILE_URL_DARK, {
      attribution: TILE_ATTR,
      maxZoom: 18,
    }).addTo(map);

    const groups = {
      participants: L.layerGroup().addTo(map),
      trails: L.layerGroup().addTo(map),
      posts: L.layerGroup().addTo(map),
      others: L.layerGroup().addTo(map),
      weather: L.layerGroup(), // NOT addTo(map) — off by default
    };
    const overlays: Record<string, L.Layer> = {
      Participants: groups.participants,
      Trails: groups.trails,
      Posts: groups.posts,
      "Other stations": groups.others,
    };
    if (weatherEnabled) overlays["Weather: Warnings"] = groups.weather;
    L.control.layers(undefined, overlays).addTo(map);
    layersRef.current = groups;
    mapRef.current = map;

    requestAnimationFrame(() => map.invalidateSize());
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(container);
    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      tileLayerRef.current = null;
      layersRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    tileLayerRef.current?.setUrl(theme === "light" ? TILE_URL_LIGHT : TILE_URL_DARK);
  }, [theme]);

  // Redraw layers on data change
  useEffect(() => {
    const map = mapRef.current;
    const groups = layersRef.current;
    if (!map || !groups) return;

    // Capture which popup was open before teardown; clearLayers() will fire
    // popupclose which would otherwise clear openPopupRef.
    const wasOpen = openPopupRef.current;
    markersRef.current.clear();

    groups.participants.clearLayers();
    groups.trails.clearLayers();
    groups.posts.clearLayers();
    groups.others.clearLayers();

    const statusByCallsign = new Map(participants.map((p) => [p.callsign, p.current_status]));
    const objectNameByPost = new Map(objects.map((o) => [o.post_id, o.name]));
    const now = Date.now();
    const allPoints: L.LatLngExpression[] = [];

    for (const post of posts) {
      if (post.lat == null || post.lon == null) continue;
      const marker = L.circleMarker([post.lat, post.lon], {
        radius: 7,
        fillColor: POST_COLOR,
        fillOpacity: 0.9,
        color: "#ffffff",
        weight: 1,
      });
      const objName = objectNameByPost.get(post.id);
      marker.bindPopup(
        popupContent(post.name, objName ? [`on the air as ${objName}`] : [], null, onToggleHide),
        { closeButton: false },
      );
      marker.addTo(groups.posts);
      allPoints.push([post.lat, post.lon]);
    }

    for (const station of stations.values()) {
      if (hidden.has(station.station_id) || station.points.length === 0) continue;
      const latest = station.points[station.points.length - 1]!;
      const stale = now - new Date(station.last_heard).getTime() > STALE_MS;
      const isParticipant = station.kind === "participant";
      const color = isParticipant
        ? STATUS_COLOR[statusByCallsign.get(station.callsign ?? "") ?? "checked_in"]
        : OTHER_COLOR;

      const marker = L.circleMarker([latest.lat, latest.lon], {
        radius: isParticipant ? 8 : 5,
        fillColor: color,
        fillOpacity: stale ? 0.3 : isParticipant ? 0.9 : 0.5,
        color: "#ffffff",
        weight: isParticipant ? 1.5 : 0.5,
      });
      const heard = new Date(station.last_heard).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
      marker.bindTooltip(station.station_id, {
        permanent: isParticipant,
        direction: "top",
        className: "font-mono text-xs",
        offset: [0, -6],
      });
      marker.bindPopup(
        popupContent(
          station.station_id,
          [
            `last heard ${heard}${stale ? " (stale)" : ""}`,
            ...(station.comment ? [station.comment] : []),
          ],
          station.station_id,
          onToggleHide,
        ),
        { closeButton: false },
      );
      marker.on("popupopen", () => {
        openPopupRef.current = station.station_id;
      });
      marker.on("popupclose", () => {
        if (openPopupRef.current === station.station_id) {
          openPopupRef.current = null;
        }
      });
      marker.addTo(isParticipant ? groups.participants : groups.others);
      markersRef.current.set(station.station_id, marker);

      if (station.points.length > 1) {
        L.polyline(
          station.points.map((p) => [p.lat, p.lon] as L.LatLngExpression),
          { color, weight: 2, opacity: 0.45, dashArray: isParticipant ? undefined : "4 4" },
        ).addTo(groups.trails);
      }
      allPoints.push([latest.lat, latest.lon]);
    }

    // Fit bounds once when content first appears; after that the operator
    // owns the viewport (refitting every poll would fight their panning).
    if (!fittedRef.current && allPoints.length > 0) {
      fittedRef.current = true;
      map.fitBounds(L.latLngBounds(allPoints), { padding: [40, 40], maxZoom: 13 });
    }

    // Re-open popup that was open before redraw, if the station still exists.
    if (wasOpen) {
      const marker = markersRef.current.get(wasOpen);
      if (marker) {
        openPopupRef.current = wasOpen;
        marker.openPopup();
      }
    }
  }, [stations, participants, posts, objects, hidden, onToggleHide]);

  // Repopulate weather warnings on alerts/enabled change
  useEffect(() => {
    const groups = layersRef.current;
    if (!groups) return;
    groups.weather.clearLayers();
    if (!weatherEnabled || alerts.features.length === 0) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    L.geoJSON(alerts as unknown as any, {
      style: (f) => alertStyle(f as unknown as WeatherFeature),
      onEachFeature: (f, layer) => {
        const p = (f as unknown as WeatherFeature).properties ?? {};
        const title = String(p.event ?? "Alert");
        const headline = String(p.headline ?? "");
        const expires = p.expires ? `<br/>Expires: ${new Date(String(p.expires)).toLocaleString()}` : "";
        layer.bindPopup(`<b>${title}</b><br/>${headline}${expires}`);
      },
    }).addTo(groups.weather);
  }, [alerts, weatherEnabled]);

  return <div ref={containerRef} className="h-full w-full rounded-md overflow-hidden" />;
}
