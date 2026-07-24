import { useCallback, useEffect, useRef, useState } from "react";
import { fetchEventPositions } from "../api/events";
import type { BeaconedObject, EventStation } from "../types";

const POLL_MS = 5000;

/**
 * Cursor-polling APRS positions. The server sends the complete station
 * roster every poll with only new points (pos_seq > since); we replace the
 * roster and append points per station, deduped by pos_seq (overlapping
 * polls return overlapping ranges — same contract as the event log).
 * Polls only while `enabled` (a map is actually mounted/expanded) and the
 * tab is visible.
 */
export function useEventPositions(eventId: number, enabled: boolean, token?: string) {
  const [stations, setStations] = useState<Map<string, EventStation>>(new Map());
  const [aprsStatus, setAprsStatus] = useState("disabled");
  const [aprsStatusDetail, setAprsStatusDetail] = useState("");
  const [objects, setObjects] = useState<BeaconedObject[]>([]);
  const sinceRef = useRef(0);
  const pointsRef = useRef<Map<string, EventStation>>(new Map());

  const refresh = useCallback(async () => {
    try {
      const u = await fetchEventPositions(eventId, sinceRef.current, token);
      const next = new Map<string, EventStation>();
      for (const station of u.stations) {
        const prev = pointsRef.current.get(station.station_id);
        const prevPoints = prev ? prev.points : [];
        const lastPoint = prevPoints[prevPoints.length - 1];
        const lastSeq = lastPoint ? lastPoint.pos_seq : 0;
        next.set(station.station_id, {
          ...station,
          points: [...prevPoints, ...station.points.filter((p) => p.pos_seq > lastSeq)],
        });
      }
      pointsRef.current = next; // roster replaced: dropped stations disappear
      sinceRef.current = Math.max(sinceRef.current, u.latest_pos_seq);
      setStations(next);
      setAprsStatus(u.aprs_status);
      setAprsStatusDetail(u.aprs_status_detail);
      setObjects(u.objects);
    } catch {
      // Keep last-known positions on a failed poll; the aprs_status badge
      // reflects backend connectivity, not this fetch.
    }
  }, [eventId, token]);

  useEffect(() => {
    if (!enabled) return;
    sinceRef.current = 0;
    pointsRef.current = new Map();
    setStations(new Map());
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh, enabled]);

  return { stations, aprsStatus, aprsStatusDetail, objects, refresh };
}
