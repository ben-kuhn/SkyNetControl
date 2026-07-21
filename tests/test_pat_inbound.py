from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

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
    assert m["received_at"].tzinfo is not None
    assert m["attachments"][0]["filename"] == "RMS_Express_Form_ICS213.xml"
    assert m["attachments"][0]["data"] == b"<RMS_Express_Form/>"
    assert m["attachments"][0]["content_type"] == "application/octet-stream"


def test_scan_one_filters_pat_by_net_address(app):
    """PAT inbound messages addressed to a different net must be dropped before import."""
    from unittest.mock import MagicMock, patch
    from datetime import date
    from backend.integrations.scanner.service import scan_one
    from backend.modules.schedule.models import NetSession, NetSeason, SessionType, SessionStatus

    with app.state.session_factory() as db:
        from backend.modules.nets.models import Net
        net = Net(slug="filtest", name="Filter Test Net")
        db.add(net)
        db.flush()
        net_id = net.id

        # Configure net_address for this net
        from backend.modules.nets.config_service import set_net_config
        set_net_config(db, net_id, "net_address", "W0NE@winlink.org")
        set_net_config(db, net_id, "scanner.enabled", "true")
        set_net_config(db, net_id, "pat_mailbox_path", "/fake/mailbox")

        # Set PAT transport enabled
        set_net_config(db, net_id, "pat_transport_enabled", "true")
        set_net_config(db, net_id, "pat_http_base_url", "http://pat.test")
        set_net_config(db, net_id, "pat_http_auth_mode", "none")
        db.commit()

        # Create an active session so check-in import runs
        season = NetSeason(net_id=net_id, name="S1", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        db.add(season)
        db.flush()
        today = date(2026, 7, 20)
        net_session = NetSession(
            season_id=season.id,
            start_date=today,
            end_date=today,
            grace_period_hours=48.0,
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
        )
        db.add(net_session)
        db.commit()

        # Two messages: one matching, one not
        matching_msg = {
            "path": "pat-http://in/M_MATCH",
            "message_id": "M_MATCH",
            "from_address": "W0ABC@winlink.org",
            "to_address": "W0NE@winlink.org",
            "subject": "Check-in",
            "received_at": datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc),
            "body": "73",
            "attachments": [],
        }
        other_net_msg = {
            "path": "pat-http://in/M_OTHER",
            "message_id": "M_OTHER",
            "from_address": "W0XYZ@winlink.org",
            "to_address": "W1OTHER@winlink.org",
            "subject": "Not for us",
            "received_at": datetime(2026, 7, 20, 18, 1, tzinfo=timezone.utc),
            "body": "hello",
            "attachments": [],
        }

        imported_messages = []

        def fake_scan_and_import(db, messages, session, net_id=None):
            imported_messages.extend(messages)
            return []

        now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)

        with (
            patch("backend.integrations.winlink.pat_inbound.fetch_inbound_messages",
                  return_value=[matching_msg, other_net_msg]),
            patch("backend.integrations.scanner.service.scan_and_import_messages",
                  side_effect=fake_scan_and_import),
        ):
            scan_one(db, net_id, "/fake/mailbox", now)

        assert len(imported_messages) == 1
        assert imported_messages[0]["message_id"] == "M_MATCH"


def test_fetch_skips_unparseable_message():
    def handler(request):
        if request.url.path == "/api/mailbox/in":
            return httpx.Response(200, json=[{"MID": "BAD"}])
        if request.url.path == "/api/mailbox/in/BAD":
            return httpx.Response(200, json={"MID": "BAD"})  # no From
        return httpx.Response(404)

    assert fetch_inbound_messages(_client(handler)) == []
