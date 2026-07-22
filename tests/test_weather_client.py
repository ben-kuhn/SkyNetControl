# tests/test_weather_client.py
import httpx
import pytest

from backend.integrations.weather.client import WeatherClient, WeatherUnavailable


def _client(handler):
    c = WeatherClient(user_agent="SkyNetControl (test@example.com)", timeout=5.0)
    c._transport = httpx.MockTransport(handler)
    return c


def test_fetch_active_alerts_merges_and_dedups_by_id():
    def handler(request: httpx.Request) -> httpx.Response:
        area = request.url.params.get("area")
        assert request.url.path == "/alerts/active"
        assert request.headers["user-agent"] == "SkyNetControl (test@example.com)"
        if area == "MN":
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [
                {"id": "A", "properties": {"event": "Tornado Warning"}},
                {"id": "B", "properties": {"event": "Flood Watch"}},
            ]})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [
            {"id": "B", "properties": {"event": "Flood Watch"}},  # dup across states
            {"id": "C", "properties": {"event": "Severe Thunderstorm Warning"}},
        ]})

    fc = _client(handler).fetch_active_alerts(["MN", "WI"])
    assert fc["type"] == "FeatureCollection"
    ids = sorted(f["id"] for f in fc["features"])
    assert ids == ["A", "B", "C"]  # B deduped


def test_lookup_state_reads_relative_location():
    def handler(request):
        assert request.url.path == "/points/44.98,-93.27"
        return httpx.Response(200, json={"properties": {
            "relativeLocation": {"properties": {"city": "Minneapolis", "state": "MN"}}}})
    assert _client(handler).lookup_state(44.98, -93.27) == "MN"


def test_lookup_state_missing_returns_none():
    def handler(request):
        return httpx.Response(200, json={"properties": {}})
    assert _client(handler).lookup_state(0.0, 0.0) is None


def test_http_error_maps_to_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(WeatherUnavailable):
        _client(handler).fetch_active_alerts(["MN"])


def test_fetch_active_alerts_http_error_raises_unavailable():
    def handler(request):
        return httpx.Response(500)
    with pytest.raises(WeatherUnavailable):
        _client(handler).fetch_active_alerts(["MN"])


def test_fetch_active_alerts_null_features_handles_gracefully():
    def handler(request):
        return httpx.Response(200, json={"features": None})
    fc = _client(handler).fetch_active_alerts(["MN"])
    assert fc["features"] == []


def test_lookup_state_malformed_properties_returns_none():
    def handler(request):
        return httpx.Response(200, json={"properties": None})
    assert _client(handler).lookup_state(0.0, 0.0) is None


def test_lookup_state_non_dict_relative_location_returns_none():
    def handler(request):
        return httpx.Response(200, json={"properties": {"relativeLocation": "str"}})
    assert _client(handler).lookup_state(0.0, 0.0) is None
