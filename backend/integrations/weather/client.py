# backend/integrations/weather/client.py
"""HTTP client seam for the US National Weather Service API (api.weather.gov).
Every NWS call goes through here with a descriptive User-Agent; tests mock the
transport, so no live network in CI."""
from __future__ import annotations

import httpx

NWS_BASE = "https://api.weather.gov"


class WeatherUnavailable(Exception):
    """NWS could not be reached or returned an error."""


class WeatherClient:
    def __init__(self, *, user_agent: str, timeout: float = 15.0):
        self.user_agent = user_agent
        self.timeout = timeout
        self._transport: httpx.BaseTransport | None = None  # test seam

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=NWS_BASE,
            headers={"User-Agent": self.user_agent, "Accept": "application/geo+json"},
            timeout=self.timeout,
            transport=self._transport,
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            with self._client() as c:
                resp = c.get(path, params=params)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise WeatherUnavailable(f"NWS {path} unavailable: {exc}") from exc

    def fetch_active_alerts(self, states: list[str]) -> dict:
        """Merge active alerts across states, deduped by feature id."""
        features: dict[str, dict] = {}
        for state in states:
            data = self._get("/alerts/active", params={"area": state})
            for feat in (data.get("features") or []):
                fid = feat.get("id") or feat.get("properties", {}).get("id")
                if fid:
                    features[fid] = feat
        return {"type": "FeatureCollection", "features": list(features.values())}

    def lookup_state(self, lat: float, lon: float) -> str | None:
        data = self._get(f"/points/{lat},{lon}")
        try:
            return data["properties"]["relativeLocation"]["properties"]["state"] or None
        except (KeyError, TypeError):
            return None
