import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.integrations.aprs.client as aprs_client
from backend.db.base import Base
from backend.integrations.aprs import manager
from backend.modules.events.models import Event, EventType
from backend.modules.events.event_config_service import set_event_config_bulk
from backend.modules.events.service import activate_event, check_in, create_event


class FakeAprsServer:
    """Minimal in-process APRS-IS stand-in: greets, records every line the
    client sends, and lets tests push packet lines to the client."""

    def __init__(self):
        self.received: list[str] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.server = None
        self.port = None
        self.connections = 0

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        self.connections += 1
        self.writers.append(writer)
        writer.write(b"# aprsc test server\r\n")
        await writer.drain()
        # Read the login line and send a verified logresp
        login = await reader.readline()
        self.received.append(login.decode().strip())
        callsign = login.decode().split()[1] if login else "NOCALL"
        writer.write(f"# logresp {callsign} verified, server T2TEST\r\n".encode())
        await writer.drain()
        while True:
            line = await reader.readline()
            if not line:
                break
            self.received.append(line.decode().strip())

    async def send(self, line: str):
        for w in self.writers:
            w.write((line + "\r\n").encode())
            await w.drain()

    async def stop(self):
        for w in self.writers:
            w.close()
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def aprs_event(db_factory, monkeypatch):
    """An active event with APRS enabled and one checked-in participant."""
    monkeypatch.setattr(
        "backend.modules.events.service.lookup_callsign", lambda db, cs: None
    )
    with db_factory() as db:
        event = create_event(
            db, name="Tornado", event_type=EventType.EMERGENCY,
            created_by="W0NE",
        )
        activate_event(db, event.id, actor="W0NE")
        set_event_config_bulk(db, event.id, {
            "aprs.enabled": "true",
            "aprs.callsign": "W0NE",
        })
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        return event.id


@pytest.fixture(autouse=True)
def _clean_states():
    manager._states.clear()
    yield
    manager._states.clear()


async def _wait_for(predicate, timeout=5.0):
    """Poll until predicate() is truthy or fail the test."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


class TestConfig:
    def test_aprs_config_reads_event_config(self, db_factory):
        with db_factory() as db:
            event = create_event(db, name="Config Test", event_type=EventType.EMERGENCY, created_by="W0NE")
            assert manager.aprs_config(db, event.id) is None  # disabled by default
            set_event_config_bulk(db, event.id, {"aprs.enabled": "true", "aprs.callsign": "W0NE"})
            cfg = manager.aprs_config(db, event.id)
            assert cfg == {"callsign": "W0NE", "server": "rotate.aprs2.net", "port": 14580}

    def test_missing_callsign_defaults_to_creator(self, db_factory):
        with db_factory() as db:
            event = create_event(db, name="Default CS Test", event_type=EventType.EMERGENCY, created_by="W0NE")
            set_event_config_bulk(db, event.id, {"aprs.enabled": "true"})
            cfg = manager.aprs_config(db, event.id)
            assert cfg is not None
            assert cfg["callsign"] == "W0NE"


class TestClientLoop:
    async def test_login_filter_and_position_ingest(self, db_factory, aprs_event):
        server = FakeAprsServer()
        await server.start()
        try:
            with db_factory() as db:
                set_event_config_bulk(db, aprs_event, {"aprs.server": "127.0.0.1", "aprs.port": str(server.port)})

            manager.ensure_started(db_factory, aprs_event)
            state = manager.get_state(aprs_event)
            assert state is not None

            await _wait_for(lambda: state.status == "connected")
            await _wait_for(lambda: len(server.received) >= 1)
            login = server.received[0]
            assert login.startswith("user W0NE pass ")
            assert "b/KE0XYZ*" in login

            # Participant position (uncompressed) flows into the store
            await server.send("KE0XYZ-9>APRS,TCPIP*:!3903.75N/09434.72W>Mobile")
            await _wait_for(lambda: state.store.latest_pos_seq >= 1)
            snap = state.store.snapshot()
            assert snap["stations"][0]["station_id"] == "KE0XYZ-9"
            assert snap["stations"][0]["kind"] == "participant"
            assert snap["stations"][0]["callsign"] == "KE0XYZ"

            # Unknown station with other-layer off → dropped
            await server.send("STRANGR-1>APRS,TCPIP*:!3900.00N/09400.00W>hi")
            await asyncio.sleep(0.2)
            assert all(s["station_id"] != "STRANGR-1" for s in state.store.snapshot()["stations"])

            # Garbage line must not kill the loop
            await server.send("not an aprs packet at all")
            await server.send("KE0XYZ-9>APRS,TCPIP*:!3904.00N/09435.00W>still here")
            await _wait_for(lambda: state.store.latest_pos_seq >= 2)
        finally:
            manager.stop(aprs_event)
            state = manager.get_state(aprs_event)
            if state and state.task:
                await asyncio.wait_for(state.task, timeout=5)
            await server.stop()

    async def test_nudge_resends_filter_after_new_checkin(self, db_factory, aprs_event, monkeypatch):
        monkeypatch.setattr(
            "backend.modules.events.service.lookup_callsign", lambda db, cs: None
        )
        server = FakeAprsServer()
        await server.start()
        try:
            with db_factory() as db:
                set_event_config_bulk(db, aprs_event, {"aprs.server": "127.0.0.1", "aprs.port": str(server.port)})

            manager.ensure_started(db_factory, aprs_event)
            state = manager.get_state(aprs_event)
            await _wait_for(lambda: state.status == "connected")

            with db_factory() as db:
                check_in(db, aprs_event, callsign="N0DES", actor="W0NC")
            manager.nudge(aprs_event)

            await _wait_for(lambda: any(
                line.startswith("#filter") and "b/N0DES*" in line for line in server.received
            ))
        finally:
            manager.stop(aprs_event)
            state = manager.get_state(aprs_event)
            if state and state.task:
                await asyncio.wait_for(state.task, timeout=5)
            await server.stop()

    async def test_reconnect_after_drop(self, db_factory, aprs_event, monkeypatch):
        monkeypatch.setattr(aprs_client, "RECONNECT_BASE_S", 0.05)
        server = FakeAprsServer()
        await server.start()
        try:
            with db_factory() as db:
                set_event_config_bulk(db, aprs_event, {"aprs.server": "127.0.0.1", "aprs.port": str(server.port)})

            manager.ensure_started(db_factory, aprs_event)
            state = manager.get_state(aprs_event)
            await _wait_for(lambda: state.status == "connected")

            # Drop the connection server-side; client must reconnect
            for w in server.writers:
                w.close()
            server.writers.clear()
            await _wait_for(lambda: server.connections >= 2)
            await _wait_for(lambda: state.status == "connected")
        finally:
            manager.stop(aprs_event)
            state = manager.get_state(aprs_event)
            if state and state.task:
                await asyncio.wait_for(state.task, timeout=5)
            await server.stop()

    async def test_ensure_started_noop_when_aprs_disabled(self, db_factory, monkeypatch):
        monkeypatch.setattr(
            "backend.modules.events.service.lookup_callsign", lambda db, cs: None
        )
        with db_factory() as db:
            event = create_event(
                db, name="E", event_type=EventType.EMERGENCY,
                created_by="W0NE",
            )
            activate_event(db, event.id, actor="W0NE")
        manager.ensure_started(db_factory, event.id)
        assert manager.get_state(event.id) is None

    async def test_unverified_login_sets_error_status(self, db_factory, aprs_event):
        """An unverified logresp must set status=error and disable TX."""

        class UnverifiedFakeAprsServer(FakeAprsServer):
            async def _handle(self, reader, writer):
                self.connections += 1
                self.writers.append(writer)
                writer.write(b"# aprsc test server\r\n")
                await writer.drain()
                login = await reader.readline()
                self.received.append(login.decode().strip())
                callsign = login.decode().split()[1] if login else "NOCALL"
                writer.write(f"# logresp {callsign} unverified, server T2TEST\r\n".encode())
                await writer.drain()
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    self.received.append(line.decode().strip())

        server = UnverifiedFakeAprsServer()
        await server.start()
        try:
            with db_factory() as db:
                set_event_config_bulk(db, aprs_event, {"aprs.server": "127.0.0.1", "aprs.port": str(server.port)})

            manager.ensure_started(db_factory, aprs_event)
            state = manager.get_state(aprs_event)
            assert state is not None

            await _wait_for(lambda: state.status == "error")
            assert "unverified" in state.status_detail
        finally:
            manager.stop(aprs_event)
            state = manager.get_state(aprs_event)
            if state and state.task:
                await asyncio.wait_for(state.task, timeout=5)
            await server.stop()
