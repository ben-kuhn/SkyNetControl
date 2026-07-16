"""The per-event APRS-IS client coroutine: connect, verified login, filtered
receive, live filter updates, and (Task 5) object beaconing.

DB reads are short synchronous queries executed in the loop thread — the same
trade-off the rest of the codebase makes (scanner)."""
import asyncio
import logging

import aprslib

from backend.integrations.aprs.protocol import (
    BEACON_INTERVAL_S,
    build_filter,
    filter_command,
    login_line,
)

logger = logging.getLogger(__name__)

RECONNECT_BASE_S = 5
RECONNECT_MAX_S = 300
READ_TIMEOUT_S = 1.0


def refresh_config(state) -> str:
    """Re-read participants + event APRS settings; returns the filter spec.
    Also updates state's classification inputs (participant set, other flag)."""
    from backend.modules.events.models import Event, EventParticipant

    with state.session_factory() as db:
        event = db.get(Event, state.event_id)
        participants = (
            db.query(EventParticipant)
            .filter(EventParticipant.event_id == state.event_id)
            .all()
        )
    state.participant_calls = {p.callsign.split("-")[0].upper() for p in participants}
    state.other_enabled = bool(event and event.aprs_other_stations)
    kwargs = {}
    if state.other_enabled and event.aprs_range_lat is not None:
        kwargs = {
            "range_lat": event.aprs_range_lat,
            "range_lon": event.aprs_range_lon,
            "range_km": event.aprs_range_km,
        }
    return build_filter(state.participant_calls, **kwargs)


def handle_line(state, raw: bytes) -> None:
    """Parse one APRS-IS line into the store. Never raises."""
    try:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text or text.startswith("#"):
            return
        try:
            packet = aprslib.parse(text)
        except (aprslib.ParseError, aprslib.UnknownFormat):
            return
        lat = packet.get("latitude")
        lon = packet.get("longitude")
        src = packet.get("from", "")
        if lat is None or lon is None or not src:
            return
        base = src.split("-")[0].upper()
        symbol = None
        if packet.get("symbol_table") and packet.get("symbol"):
            symbol = f"{packet['symbol_table']}{packet['symbol']}"
        comment = packet.get("comment") or None
        if base in state.participant_calls:
            state.store.add_point(
                src.upper(), lat, lon, kind="participant", callsign=base,
                symbol=symbol, comment=comment,
            )
        elif state.other_enabled:
            state.store.add_point(src.upper(), lat, lon, kind="other", symbol=symbol, comment=comment)
    except Exception:  # noqa: BLE001 — one bad packet must never kill the loop
        logger.debug("Unhandled error for APRS line %r", raw, exc_info=True)


async def _send(writer, line: str) -> None:
    writer.write((line + "\r\n").encode())
    await writer.drain()


async def _send_objects(state, writer, config, *, force: bool = False) -> None:
    from backend.integrations.aprs.beacon import send_objects

    await send_objects(state, writer, config, force=force)


async def _kill_all_objects(state, writer, config) -> None:
    from backend.integrations.aprs.beacon import kill_all

    await kill_all(state, writer, config)


async def run_event_client(state, config) -> None:
    loop = asyncio.get_running_loop()
    backoff = RECONNECT_BASE_S
    try:
        while state.running:
            writer = None
            try:
                reader, writer = await asyncio.open_connection(config["server"], config["port"])
                await reader.readline()  # server banner
                current_spec = refresh_config(state)
                await _send(writer, login_line(config["callsign"], current_spec))
                logresp = (await asyncio.wait_for(reader.readline(), timeout=10)).decode("utf-8", errors="replace")
                if "unverified" in logresp:
                    # Verified login is required for object TX; APRS-IS silently
                    # drops transmissions from unverified logins.
                    state.status = "error"
                    state.status_detail = "APRS-IS login unverified (check callsign) — transmit disabled"
                else:
                    state.status = "connected"
                    state.status_detail = ""
                backoff = RECONNECT_BASE_S
                state.announced.clear()
                state.objects_by_post.clear()
                if state.status == "connected":
                    await _send_objects(state, writer, config, force=True)
                next_beacon = loop.time() + BEACON_INTERVAL_S

                while state.running:
                    try:
                        line = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT_S)
                        if line == b"":
                            raise ConnectionError("connection closed by server")
                        handle_line(state, line)
                    except asyncio.TimeoutError:
                        pass

                    if state.dirty.is_set():
                        state.dirty.clear()
                        if not state.running:
                            break
                        new_spec = refresh_config(state)
                        if new_spec != current_spec:
                            current_spec = new_spec
                            await _send(writer, filter_command(new_spec or "b/NOCALL"))
                        if not state.other_enabled:
                            state.store.drop_others()
                        if state.status == "connected":
                            await _send_objects(state, writer, config)

                    if loop.time() >= next_beacon:
                        if state.status == "connected":
                            await _send_objects(state, writer, config, force=True)
                        next_beacon = loop.time() + BEACON_INTERVAL_S

                # Clean stop: remove our objects from the network first.
                if state.status == "connected":
                    await _kill_all_objects(state, writer, config)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if not state.running:
                    break
                state.status = "reconnecting"
                state.status_detail = str(exc)
                logger.warning("APRS client for event %s: %s (retry in %ss)", state.event_id, exc, backoff)
                try:
                    await asyncio.wait_for(state.dirty.wait(), timeout=backoff)
                    state.dirty.clear()
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, RECONNECT_MAX_S)
            finally:
                if writer is not None:
                    writer.close()
    finally:
        state.status = "disabled"
        logger.info("APRS client stopped for event %s", state.event_id)
