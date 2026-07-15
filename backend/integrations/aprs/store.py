"""Per-event in-memory position store. Deliberately unpersisted: dies with
the event's client task or a server restart (spec decision)."""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

TRAIL_MAX_POINTS = 120
OTHER_STATIONS_CAP = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TrackPoint:
    lat: float
    lon: float
    ts: datetime
    pos_seq: int


@dataclass
class Station:
    station_id: str  # full callsign-SSID, e.g. "KE0XYZ-9"
    kind: str  # "participant" | "other"
    callsign: str | None  # participant base callsign (None for others)
    symbol: str | None
    comment: str | None
    last_heard: datetime
    points: deque = field(default_factory=lambda: deque(maxlen=TRAIL_MAX_POINTS))


class EventPositionStore:
    def __init__(self):
        self._participants: dict[str, Station] = {}
        self._others: OrderedDict[str, Station] = OrderedDict()
        self._seq = 0

    @property
    def latest_pos_seq(self) -> int:
        return self._seq

    def add_point(
        self,
        station_id: str,
        lat: float,
        lon: float,
        *,
        kind: str,
        callsign: str | None = None,
        symbol: str | None = None,
        comment: str | None = None,
        ts: datetime | None = None,
    ) -> int:
        ts = ts or _utcnow()
        self._seq += 1
        pool = self._participants if kind == "participant" else self._others
        station = pool.get(station_id)
        if station is None:
            station = Station(
                station_id=station_id, kind=kind, callsign=callsign,
                symbol=symbol, comment=comment, last_heard=ts,
            )
            pool[station_id] = station
        station.last_heard = ts
        if symbol is not None:
            station.symbol = symbol
        if comment is not None:
            station.comment = comment
        station.points.append(TrackPoint(lat=lat, lon=lon, ts=ts, pos_seq=self._seq))

        if kind == "other":
            self._others.move_to_end(station_id)
            while len(self._others) > OTHER_STATIONS_CAP:
                self._others.popitem(last=False)
        return self._seq

    def drop_others(self) -> None:
        self._others.clear()

    def snapshot(self, since: int = 0) -> dict:
        stations = []
        for station in list(self._participants.values()) + list(self._others.values()):
            stations.append({
                "station_id": station.station_id,
                "kind": station.kind,
                "callsign": station.callsign,
                "symbol": station.symbol,
                "comment": station.comment,
                "last_heard": station.last_heard.isoformat(),
                "points": [
                    {"lat": p.lat, "lon": p.lon, "ts": p.ts.isoformat(), "pos_seq": p.pos_seq}
                    for p in station.points
                    if p.pos_seq > since
                ],
            })
        return {"stations": stations, "latest_pos_seq": self._seq}
