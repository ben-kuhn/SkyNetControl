"""Registry and lifecycle for per-event APRS-IS client tasks.

ensure_started/stop/nudge are safe to call from sync code running inside the
FastAPI event loop (route handlers); without a running loop they no-op, so
tests and CLI paths never accidentally open sockets.
"""
import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.integrations.aprs.store import EventPositionStore
from backend.modules.events.event_config_service import get_event_config, event_from_callsign
from backend.modules.events.models import Event

logger = logging.getLogger(__name__)


@dataclass
class AprsClientState:
    event_id: int
    session_factory: object
    store: EventPositionStore = field(default_factory=EventPositionStore)
    status: str = "reconnecting"  # connected | reconnecting | error | disabled
    status_detail: str = ""
    running: bool = False
    dirty: asyncio.Event = field(default_factory=asyncio.Event)
    participant_calls: set = field(default_factory=set)
    other_enabled: bool = False
    announced: dict = field(default_factory=dict)  # object name -> (lat, lon)
    objects_by_post: dict = field(default_factory=dict)  # post_id -> object name
    task: asyncio.Task | None = None


_states: dict[int, AprsClientState] = {}


def get_state(event_id: int) -> AprsClientState | None:
    return _states.get(event_id)


def aprs_config(db: Session, event_id: int) -> dict | None:
    """The event's APRS connection settings, or None when APRS is off/unusable."""
    if get_event_config(db, event_id, "aprs.enabled", "false") != "true":
        return None
    callsign = (get_event_config(db, event_id, "aprs.callsign", "") or "").strip()
    if not callsign:
        event = db.get(Event, event_id)
        callsign = event_from_callsign(db, event) if event else ""
    if not callsign:
        return None
    server = get_event_config(db, event_id, "aprs.server", "rotate.aprs2.net") or "rotate.aprs2.net"
    try:
        port = int(get_event_config(db, event_id, "aprs.port", "14580"))
    except (TypeError, ValueError):
        port = 14580
    return {"callsign": callsign, "server": server, "port": port}


def ensure_started(session_factory, event_id: int) -> None:
    existing = _states.get(event_id)
    if existing is not None and existing.running:
        return
    from backend.modules.events.models import EventStatus

    with session_factory() as db:
        event = db.get(Event, event_id)
        if event is None or event.status != EventStatus.ACTIVE:
            return
        config = aprs_config(db, event.id)
    if config is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (sync tests, CLI) — APRS simply doesn't run
    from backend.integrations.aprs.client import run_event_client

    state = AprsClientState(event_id=event_id, session_factory=session_factory)
    state.running = True
    state.task = loop.create_task(run_event_client(state, config))
    _states[event_id] = state
    logger.info("APRS client started for event %s", event_id)


def stop(event_id: int) -> None:
    state = _states.get(event_id)
    if state is None:
        return
    state.running = False
    state.dirty.set()  # wake the loop so it can kill objects and exit


def nudge(event_id: int) -> None:
    state = _states.get(event_id)
    if state is not None:
        state.dirty.set()


def start_for_active_events(session_factory) -> None:
    """Boot-time: resume clients for events that were active at shutdown."""
    from backend.modules.events.models import Event, EventStatus

    with session_factory() as db:
        active_ids = [e.id for e in db.query(Event).filter(Event.status == EventStatus.ACTIVE).all()]
    for event_id in active_ids:
        ensure_started(session_factory, event_id)


async def shutdown_all() -> None:
    for state in list(_states.values()):
        state.running = False
        state.dirty.set()
    for state in list(_states.values()):
        if state.task is not None:
            try:
                await asyncio.wait_for(state.task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                state.task.cancel()
    _states.clear()
