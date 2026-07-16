"""Object beaconing: event posts with coordinates go out as APRS objects
under the net's callsign — only while the event is active AND the per-event
aprs_beacon_posts toggle is on. Kills are sent when objects disappear."""
import logging

from backend.integrations.aprs.protocol import object_name, object_packet

logger = logging.getLogger(__name__)


def desired_objects(state) -> dict:
    """name -> (lat, lon, post_id) for every post that should be on the air."""
    from backend.modules.events.models import Event, EventPost, EventStatus

    with state.session_factory() as db:
        event = db.get(Event, state.event_id)
        if event is None or event.status != EventStatus.ACTIVE or not event.aprs_beacon_posts:
            return {}
        posts = (
            db.query(EventPost)
            .filter(EventPost.event_id == state.event_id)
            .order_by(EventPost.id)
            .all()
        )
        desired: dict = {}
        # Deterministic naming: posts ordered by id, names uniquified in that
        # order — the same post set always produces the same object names.
        for post in posts:
            if post.lat is None or post.lon is None:
                continue
            name = object_name(post.name, set(desired.keys()))
            desired[name] = (post.lat, post.lon, post.id)
        comment_event_name = event.name
    state._beacon_comment = f"SkyNetControl event: {comment_event_name}"
    return desired


async def _send(writer, line: str) -> None:
    writer.write((line + "\r\n").encode())
    await writer.drain()


async def send_objects(state, writer, config, *, force: bool = False) -> None:
    desired = desired_objects(state)
    comment = getattr(state, "_beacon_comment", "SkyNetControl event")

    # Kills first: anything announced that's no longer desired.
    for name in [n for n in state.announced if n not in desired]:
        lat, lon = state.announced[name]
        await _send(writer, object_packet(config["callsign"], name, lat, lon, "", kill=True))
        del state.announced[name]

    # Then live objects: new, moved, or everything when forced.
    for name, (lat, lon, post_id) in desired.items():
        if force or state.announced.get(name) != (lat, lon):
            await _send(writer, object_packet(config["callsign"], name, lat, lon, comment))
        state.announced[name] = (lat, lon)

    state.objects_by_post = {post_id: name for name, (_lat, _lon, post_id) in desired.items()}


async def kill_all(state, writer, config) -> None:
    for name, (lat, lon) in list(state.announced.items()):
        try:
            await _send(writer, object_packet(config["callsign"], name, lat, lon, "", kill=True))
        except Exception:  # noqa: BLE001 — orphaned objects age out on other clients
            logger.warning("Failed to send kill for object %s (event %s)", name, state.event_id)
    state.announced.clear()
    state.objects_by_post.clear()
