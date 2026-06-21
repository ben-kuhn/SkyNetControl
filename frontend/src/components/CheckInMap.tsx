import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { CheckIn } from "../types";

const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/">CARTO</a>';

const DEFAULT_CENTER: L.LatLngExpression = [39.8283, -98.5795]; // US center
const DEFAULT_ZOOM = 4;

const ACCENT = "#22d3ee";
const WARNING = "#fbbf24";

interface Props {
  checkins: CheckIn[];
  selectedCheckinId: number | null;
  onSelectCheckin: (id: number) => void;
}

export function CheckInMap({ checkins, selectedCheckinId, onSelectCheckin }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<Map<number, L.CircleMarker>>(new Map());

  // Initialize map once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const container = containerRef.current;
    const map = L.map(container, {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      zoomControl: true,
      attributionControl: true,
    });

    L.tileLayer(TILE_URL, { attribution: TILE_ATTR, maxZoom: 18 }).addTo(map);
    mapRef.current = map;

    // If the container's parent settles to its real size *after* mount
    // (flex layout still resolving, lazy-mounted tab, etc.), Leaflet
    // initializes with a 0×0 viewport and never requests tiles — looks
    // blank. invalidateSize on the next frame + on any future container
    // resize keeps the tile grid in sync. ResizeObserver covers
    // orientation changes on mobile too.
    requestAnimationFrame(() => map.invalidateSize());
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(container);

    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Render markers when checkins change
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // Clear existing markers
    markersRef.current.forEach((m) => m.remove());
    markersRef.current.clear();

    const withCoords = checkins.filter(
      (c): c is CheckIn & { latitude: number; longitude: number } =>
        c.latitude != null && c.longitude != null,
    );

    if (withCoords.length === 0) {
      map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
      return;
    }

    for (const c of withCoords) {
      const isSelected = c.id === selectedCheckinId;
      const marker = L.circleMarker([c.latitude, c.longitude], {
        radius: isSelected ? 10 : 6,
        fillColor: c.is_new_member ? WARNING : ACCENT,
        fillOpacity: isSelected ? 1 : 0.6,
        color: isSelected ? "#ffffff" : "transparent",
        weight: isSelected ? 2 : 0,
      });

      marker.bindPopup(
        `<strong style="font-family:monospace">${c.callsign}</strong><br/>${c.name}`,
        { closeButton: false, className: "checkin-popup" },
      );

      marker.on("click", () => onSelectCheckin(c.id));
      marker.addTo(map);
      markersRef.current.set(c.id, marker);
    }

    // Fit bounds to all markers
    const bounds = L.latLngBounds(withCoords.map((c) => [c.latitude, c.longitude]));
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
  }, [checkins, selectedCheckinId, onSelectCheckin]);

  // Pan to selected marker and open popup
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selectedCheckinId) return;

    const marker = markersRef.current.get(selectedCheckinId);
    if (marker) {
      map.panTo(marker.getLatLng(), { animate: true });
      marker.openPopup();
    }
  }, [selectedCheckinId]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full min-h-[400px] rounded-lg border border-border overflow-hidden"
    />
  );
}
