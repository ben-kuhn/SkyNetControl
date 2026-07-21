import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.winlink.models import PatConnectionSession, PatSessionStatus
from backend.integrations.winlink.pat_client import PatConnectError, PatUnavailable
from backend.integrations.winlink import pat_session


def _factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class FakeClient:
    def __init__(self, *, out_box=None, connect_ok=True, unreachable=False):
        self._out_box = out_box if out_box is not None else []
        self._connect_ok = connect_ok
        self._unreachable = unreachable
        self.disconnected = False

    def connect(self, url):
        if self._unreachable:
            raise PatUnavailable("down")
        if not self._connect_ok:
            raise PatConnectError("no link")
        return True

    def disconnect(self):
        self.disconnected = True

    def list_mailbox(self, box):
        return self._out_box if box == "out" else []


@pytest.fixture(autouse=True)
def _reset_engine():
    pat_session.engine = pat_session.PatSessionEngine()
    yield


async def _seed_session(factory, **over):
    with factory() as db:
        s = PatConnectionSession(
            net_id=over.get("net_id", 1), event_id=None,
            connect_url="telnet:///", method_label="alias: telnet",
            status=PatSessionStatus.CONNECTING, sent_count=0, received_count=0,
            events=[], actor="W0NC", started_at=datetime.now(tz=timezone.utc),
        )
        db.add(s); db.commit()
        return s.id


async def test_successful_session_marks_completed(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(pat_session, "scan_all_enabled", lambda db, now: 2)
    sid = await _seed_session(factory)
    await pat_session.engine.run_session(factory, sid, FakeClient(), timeout=5)
    with factory() as db:
        s = db.get(PatConnectionSession, sid)
        assert s.status == PatSessionStatus.COMPLETED
        assert s.received_count == 2
        assert s.ended_at is not None


async def test_connect_failure_marks_failed(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(pat_session, "scan_all_enabled", lambda db, now: 0)
    sid = await _seed_session(factory)
    await pat_session.engine.run_session(factory, sid, FakeClient(connect_ok=False), timeout=5)
    with factory() as db:
        assert db.get(PatConnectionSession, sid).status == PatSessionStatus.FAILED


async def test_reconcile_flips_queued_to_sent(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(pat_session, "scan_all_enabled", lambda db, now: 0)
    with factory() as db:
        db.add(DeliveryLog(content_type="event_message", content_id=1, backend="winlink",
                           status=DeliveryStatus.QUEUED, pat_mid="GONE",
                           created_at=datetime.now(tz=timezone.utc)))
        db.add(DeliveryLog(content_type="event_message", content_id=2, backend="winlink",
                           status=DeliveryStatus.QUEUED, pat_mid="STILL",
                           created_at=datetime.now(tz=timezone.utc)))
        db.commit()
    sid = await _seed_session(factory)
    # PAT's out box still holds STILL; GONE was sent.
    client = FakeClient(out_box=[{"MID": "STILL"}])
    await pat_session.engine.run_session(factory, sid, client, timeout=5)
    with factory() as db:
        rows = {r.pat_mid: r.status for r in db.query(DeliveryLog).all()}
        assert rows["GONE"] == DeliveryStatus.SENT
        assert rows["STILL"] == DeliveryStatus.QUEUED
        assert db.get(PatConnectionSession, sid).sent_count == 1


async def test_single_flight_blocks_second_start(monkeypatch):
    factory = _factory()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run(f, sid, client, timeout):
        started.set()
        await release.wait()

    monkeypatch.setattr(pat_session.engine, "run_session", slow_run)
    await pat_session.engine.start(factory, net_id=1, event_id=None, actor="W0NC",
                                   connect_url="telnet:///", method_label="x", client=FakeClient())
    await started.wait()
    with pytest.raises(pat_session.SessionBusy):
        await pat_session.engine.start(factory, net_id=1, event_id=None, actor="W0NC",
                                       connect_url="telnet:///", method_label="x", client=FakeClient())
    release.set()
    await pat_session.engine.shutdown()
