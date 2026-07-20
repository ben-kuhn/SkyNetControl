# tests/test_pat_client.py
import httpx
import pytest

from backend.integrations.winlink.pat_client import (
    PatAuth, PatClient, PatUnavailable, PatConnectError,
)


def _client(handler, auth=None):
    transport = httpx.MockTransport(handler)
    c = PatClient("http://pat.test:8080", auth=auth, timeout=5.0)
    c._transport = transport  # test seam: injected transport for the sync httpx.Client
    return c


def test_post_outbound_sends_multipart_and_returns_mid():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(200, json={"MID": "ABC123"})

    c = _client(handler)
    mid = c.post_outbound(
        to="KE0XYZ", subject="Hi", body="hello", cc=[],
        attachments=[{"filename": "RMS_Express_Form_ICS213.xml", "content_type": "application/octet-stream", "data": b"<x/>"}],
    )
    assert mid == "ABC123"
    assert seen["url"] == "http://pat.test:8080/api/mailbox/out"
    assert seen["method"] == "POST"
    assert "multipart/form-data" in seen["content_type"]
    assert b"KE0XYZ" in seen["body"]
    assert b"RMS_Express_Form_ICS213.xml" in seen["body"]


def test_connect_success_and_failure():
    def ok(request): return httpx.Response(200, json={"success": True})
    assert _client(ok).connect("telnet:///") is True

    def bad(request): return httpx.Response(200, json={"success": False})
    with pytest.raises(PatConnectError):
        _client(bad).connect("telnet:///")


def test_transport_error_maps_to_unavailable():
    def boom(request): raise httpx.ConnectError("refused")
    with pytest.raises(PatUnavailable):
        _client(boom).status()


def test_basic_auth_header_injected():
    seen = {}
    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})
    _client(handler, auth=PatAuth("basic", "op", "pw", "")).status()
    assert seen["auth"].startswith("Basic ")


def test_token_auth_header_injected():
    seen = {}
    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})
    _client(handler, auth=PatAuth("token", "", "", "tok123")).status()
    assert seen["auth"] == "Bearer tok123"


def test_connect_aliases_and_rmslist_parse():
    def handler(request):
        if request.url.path == "/api/config/connect_aliases":
            return httpx.Response(200, json={"gw1": "ardop:///KE0GW?freq=7100"})
        if request.url.path == "/api/rmslist":
            return httpx.Response(200, json=[{"callsign": "KE0GW", "modes": "ARDOP", "dial": 7100000}])
        return httpx.Response(404)
    c = _client(handler)
    assert c.connect_aliases() == {"gw1": "ardop:///KE0GW?freq=7100"}
    assert c.rmslist()[0]["callsign"] == "KE0GW"
