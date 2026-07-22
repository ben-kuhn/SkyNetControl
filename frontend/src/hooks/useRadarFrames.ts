import { useCallback, useEffect, useState } from "react";

const INDEX_URL = "https://api.rainviewer.com/public/weather-maps.json";
const REFETCH_MS = 5 * 60 * 1000;

export interface RadarFrame { time: number; path: string; }

interface RadarState {
  frames: RadarFrame[];
  tileUrl: (frame: RadarFrame) => string;
}

const EMPTY: RadarState = { frames: [], tileUrl: () => "" };

export function useRadarFrames(enabled: boolean): RadarState {
  const [state, setState] = useState<RadarState>(EMPTY);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(INDEX_URL);
      if (!res.ok) return;
      const json = await res.json();
      const host: string = json.host;
      const past: RadarFrame[] = json?.radar?.past ?? [];
      const nowcast: RadarFrame[] = json?.radar?.nowcast ?? [];
      const frames = [...past, ...nowcast];
      // RainViewer tile template: {host}{path}/{size}/{z}/{x}/{y}/{color}/{smooth}_{snow}.png
      const tileUrl = (frame: RadarFrame) => `${host}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`;
      setState({ frames, tileUrl });
    } catch {
      // leave last-known frames on failure
    }
  }, []);

  useEffect(() => {
    if (!enabled) { setState(EMPTY); return; }
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, REFETCH_MS);
    return () => window.clearInterval(id);
  }, [refresh, enabled]);

  return state;
}
