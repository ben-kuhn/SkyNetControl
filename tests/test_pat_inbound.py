from datetime import datetime

import httpx

from backend.integrations.winlink.pat_client import PatAuth, PatClient
from backend.integrations.winlink.pat_inbound import fetch_inbound_messages


def _client(handler):
    c = PatClient("http://pat.test", auth=PatAuth("none"), timeout=5.0)
    c._transport = httpx.MockTransport(handler)
    return c


def test_fetch_maps_messages_and_attachments():
    def handler(request):
        p = request.url.path
        if p == "/api/mailbox/in":
            return httpx.Response(200, json=[{"MID": "M1"}])
        if p == "/api/mailbox/in/M1":
            return httpx.Response(200, json={
                "MID": "M1", "From": "W0ABC@winlink.org", "To": "W0NE@winlink.org",
                "Subject": "Check-in", "Date": "2026-07-20T18:30:00Z",
                "Body": "all good",
                "Files": [{"Name": "RMS_Express_Form_ICS213.xml"}],
            })
        if p == "/api/mailbox/in/M1/RMS_Express_Form_ICS213.xml":
            return httpx.Response(200, content=b"<RMS_Express_Form/>")
        return httpx.Response(404)

    msgs = fetch_inbound_messages(_client(handler))
    assert len(msgs) == 1
    m = msgs[0]
    assert m["message_id"] == "M1"
    assert m["from_address"] == "W0ABC@winlink.org"
    assert m["to_address"] == "W0NE@winlink.org"
    assert m["subject"] == "Check-in"
    assert m["body"] == "all good"
    assert isinstance(m["received_at"], datetime)
    assert m["attachments"][0]["filename"] == "RMS_Express_Form_ICS213.xml"
    assert m["attachments"][0]["data"] == b"<RMS_Express_Form/>"


def test_fetch_skips_unparseable_message():
    def handler(request):
        if request.url.path == "/api/mailbox/in":
            return httpx.Response(200, json=[{"MID": "BAD"}])
        if request.url.path == "/api/mailbox/in/BAD":
            return httpx.Response(200, json={"MID": "BAD"})  # no From
        return httpx.Response(404)

    assert fetch_inbound_messages(_client(handler)) == []
