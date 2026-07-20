import httpx

from backend.integrations.winlink.pat_config import PatHttpConfig
from backend.integrations.winlink.pat_client import PatAuth, PatClient
from backend.integrations.delivery.backends.winlink import WinlinkBackend


def _cfg_with_client(handler):
    client = PatClient("http://pat.test", auth=PatAuth("none"), timeout=5.0)
    client._transport = httpx.MockTransport(handler)
    cfg = PatHttpConfig(base_url="http://pat.test", auth=PatAuth("none"), timeout=5.0, enabled=True)
    return cfg, client


def test_send_posts_via_http_and_returns_queued():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"MID": "M1"})

    cfg, client = _cfg_with_client(handler)
    result = WinlinkBackend().send("Subj", "Body", {
        "target_address": "KE0XYZ", "callsign": "W0NE",
        "attachments": [{"filename": "f.xml", "content_type": "application/octet-stream", "data": b"<x/>"}],
        "pat_http": cfg, "pat_client": client,
    })
    assert result.success is True
    assert result.queued is True
    assert result.pat_mid == "M1"
    assert seen["path"] == "/api/mailbox/out"
    assert b"KE0XYZ" in seen["body"]


def test_send_falls_back_to_file_when_disabled(tmp_path):
    result = WinlinkBackend().send("Subj", "Body", {
        "mailbox_path": str(tmp_path), "target_address": "KE0XYZ", "callsign": "W0NE",
    })
    assert result.success is True
    assert result.queued is False
    assert (tmp_path / "out").exists()


def test_http_failure_returns_error_not_success():
    def handler(request):
        raise httpx.ConnectError("refused")

    cfg, client = _cfg_with_client(handler)
    result = WinlinkBackend().send("Subj", "Body", {
        "target_address": "KE0XYZ", "callsign": "W0NE",
        "pat_http": cfg, "pat_client": client,
    })
    assert result.success is False
    assert result.queued is False
    assert result.error
