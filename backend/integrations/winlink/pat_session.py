# backend/integrations/winlink/pat_session.py
"""Background PAT connect/session engine. One session at a time (single radio);
runs the connect in a thread, reconciles QUEUED->SENT from PAT's outbox, imports
inbound, and records status. Live /ws progress is layered on in a later task."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.scanner.service import scan_all_enabled
from backend.integrations.winlink.models import PatConnectionSession, PatSessionStatus
from backend.integrations.winlink.pat_client import PatClient, PatConnectError, PatUnavailable

logger = logging.getLogger(__name__)


class SessionBusy(Exception):
    """A connect session is already running (single radio, single-flight)."""


def _set_status(db, session_id, status, **fields):
    s = db.get(PatConnectionSession, session_id)
    s.status = status
    for k, v in fields.items():
        setattr(s, k, v)
    db.commit()


def _reconcile_outbound(db, client: PatClient, session_id: int) -> int:
    """Flip QUEUED winlink deliveries to SENT when their pat_mid is no longer in
    PAT's out box. Returns the count flipped. Best-effort: on any PAT error the
    rows stay QUEUED (safe)."""
    try:
        still_out = {str(m.get("MID") or m.get("mid") or "") for m in client.list_mailbox("out")}
    except PatUnavailable:
        return 0
    flipped = 0
    queued = (db.query(DeliveryLog)
                .filter(DeliveryLog.backend == "winlink",
                        DeliveryLog.status == DeliveryStatus.QUEUED).all())
    for row in queued:
        if row.pat_mid and row.pat_mid not in still_out:
            row.status = DeliveryStatus.SENT
            row.sent_at = datetime.now(tz=timezone.utc)
            row.pat_session_id = session_id
            flipped += 1
    db.commit()
    return flipped


class PatSessionEngine:
    def __init__(self):
        self._active_task: asyncio.Task | None = None
        self.active_session_id: int | None = None

    async def start(self, session_factory, *, net_id, event_id, actor,
                    connect_url, method_label, client) -> int:
        if self._active_task is not None and not self._active_task.done():
            raise SessionBusy("A PAT connection is already in progress")
        with session_factory() as db:
            s = PatConnectionSession(
                net_id=net_id, event_id=event_id, connect_url=connect_url,
                method_label=method_label, status=PatSessionStatus.CONNECTING,
                sent_count=0, received_count=0, events=[], actor=actor,
                started_at=datetime.now(tz=timezone.utc),
            )
            db.add(s)
            db.commit()
            session_id = s.id
        self.active_session_id = session_id
        self._active_task = asyncio.create_task(
            self.run_session(session_factory, session_id, client, timeout=300)
        )
        return session_id

    async def run_session(self, session_factory, session_id, client: PatClient, timeout: int) -> None:
        try:
            with session_factory() as db:
                _set_status(db, session_id, PatSessionStatus.CONNECTED)
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(client.connect, _connect_url(session_factory, session_id)),
                    timeout=timeout,
                )
            except (PatConnectError, PatUnavailable) as exc:
                with session_factory() as db:
                    _set_status(db, session_id, PatSessionStatus.FAILED,
                                error=str(exc), ended_at=datetime.now(tz=timezone.utc))
                return
            except asyncio.TimeoutError:
                try:
                    await asyncio.to_thread(client.disconnect)
                except Exception:
                    pass
                with session_factory() as db:
                    _set_status(db, session_id, PatSessionStatus.FAILED,
                                error="session timed out",
                                ended_at=datetime.now(tz=timezone.utc))
                return

            with session_factory() as db:
                _set_status(db, session_id, PatSessionStatus.SYNCING)
                sent = _reconcile_outbound(db, client, session_id)
            received = 0
            try:
                with session_factory() as db:
                    received = scan_all_enabled(db, datetime.now(tz=timezone.utc)) or 0
            except Exception:
                logger.exception("inbound import during session %s failed", session_id)
            with session_factory() as db:
                _set_status(db, session_id, PatSessionStatus.COMPLETED,
                            sent_count=sent, received_count=received,
                            ended_at=datetime.now(tz=timezone.utc))
        finally:
            self.active_session_id = None

    async def abort(self, session_factory, session_id, client: PatClient) -> None:
        try:
            await asyncio.to_thread(client.disconnect)
        except Exception:
            pass
        with session_factory() as db:
            s = db.get(PatConnectionSession, session_id)
            if s and s.status not in (PatSessionStatus.COMPLETED, PatSessionStatus.FAILED):
                _set_status(db, session_id, PatSessionStatus.ABORTED,
                            ended_at=datetime.now(tz=timezone.utc))

    async def shutdown(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()
            try:
                await self._active_task
            except (asyncio.CancelledError, Exception):
                pass
        self._active_task = None
        self.active_session_id = None


def _connect_url(session_factory, session_id: int) -> str:
    with session_factory() as db:
        return db.get(PatConnectionSession, session_id).connect_url


engine = PatSessionEngine()
