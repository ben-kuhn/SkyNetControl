import { useCallback, useEffect, useState } from "react";
import { fetchEventWeather } from "../api/events";
import type { WeatherData } from "../types";

const POLL_MS = 60000;
const EMPTY: WeatherData = { alerts: { type: "FeatureCollection", features: [] }, updated_at: null, status: "disabled" };

export function useEventWeather(eventId: number, enabled: boolean, token?: string): WeatherData {
  const [data, setData] = useState<WeatherData>(EMPTY);

  const refresh = useCallback(async () => {
    try {
      setData(await fetchEventWeather(eventId, token));
    } catch {
      // keep last-known on transient failure
    }
  }, [eventId, token]);

  useEffect(() => {
    if (!enabled) { setData(EMPTY); return; }
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh, enabled]);

  return data;
}
