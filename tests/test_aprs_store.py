from datetime import datetime, timezone

from backend.integrations.aprs.store import (
    OTHER_STATIONS_CAP,
    TRAIL_MAX_POINTS,
    EventPositionStore,
)


def _ts(minute):
    return datetime(2026, 7, 15, 18, minute % 60, 0, tzinfo=timezone.utc)


class TestAddAndSnapshot:
    def test_pos_seq_monotonic(self):
        store = EventPositionStore()
        s1 = store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        s2 = store.add_point("KE0XYZ-9", 39.1, -94.1, kind="participant", callsign="KE0XYZ")
        assert (s1, s2) == (1, 2)
        assert store.latest_pos_seq == 2

    def test_snapshot_full_and_delta(self):
        store = EventPositionStore()
        store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ", ts=_ts(0))
        store.add_point("KE0XYZ-9", 39.1, -94.1, kind="participant", callsign="KE0XYZ", ts=_ts(1))
        full = store.snapshot(since=0)
        assert len(full["stations"]) == 1
        assert len(full["stations"][0]["points"]) == 2
        assert full["latest_pos_seq"] == 2

        delta = store.snapshot(since=1)
        assert len(delta["stations"][0]["points"]) == 1
        assert delta["stations"][0]["points"][0]["pos_seq"] == 2

    def test_roster_always_complete_even_with_no_new_points(self):
        store = EventPositionStore()
        store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        snap = store.snapshot(since=99)
        assert len(snap["stations"]) == 1
        assert snap["stations"][0]["points"] == []
        assert snap["stations"][0]["station_id"] == "KE0XYZ-9"

    def test_station_metadata(self):
        store = EventPositionStore()
        store.add_point(
            "KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ",
            symbol="/>", comment="mobile", ts=_ts(5),
        )
        st = store.snapshot()["stations"][0]
        assert st["kind"] == "participant"
        assert st["callsign"] == "KE0XYZ"
        assert st["symbol"] == "/>"
        assert st["comment"] == "mobile"
        assert st["last_heard"] == _ts(5).isoformat()

    def test_trail_bounded(self):
        store = EventPositionStore()
        for i in range(TRAIL_MAX_POINTS + 30):
            store.add_point("W0NE-9", 39.0 + i * 0.001, -94.0, kind="participant", callsign="W0NE")
        pts = store.snapshot()["stations"][0]["points"]
        assert len(pts) == TRAIL_MAX_POINTS
        # oldest points dropped, newest kept
        assert pts[-1]["pos_seq"] == TRAIL_MAX_POINTS + 30


class TestOthers:
    def test_lru_cap(self):
        store = EventPositionStore()
        for i in range(OTHER_STATIONS_CAP + 10):
            store.add_point(f"X{i}", 39.0, -94.0, kind="other")
        others = [s for s in store.snapshot()["stations"] if s["kind"] == "other"]
        assert len(others) == OTHER_STATIONS_CAP
        ids = {s["station_id"] for s in others}
        assert "X0" not in ids  # evicted
        assert f"X{OTHER_STATIONS_CAP + 9}" in ids

    def test_readd_refreshes_lru(self):
        store = EventPositionStore()
        for i in range(OTHER_STATIONS_CAP):
            store.add_point(f"X{i}", 39.0, -94.0, kind="other")
        store.add_point("X0", 39.5, -94.5, kind="other")  # refresh oldest
        store.add_point("NEW", 39.0, -94.0, kind="other")  # evicts X1, not X0
        ids = {s["station_id"] for s in store.snapshot()["stations"]}
        assert "X0" in ids
        assert "X1" not in ids

    def test_drop_others_keeps_participants(self):
        store = EventPositionStore()
        store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        store.add_point("STRANGER", 39.0, -94.0, kind="other")
        store.drop_others()
        stations = store.snapshot()["stations"]
        assert len(stations) == 1
        assert stations[0]["kind"] == "participant"


class TestKindPromotion:
    def test_other_promoted_to_participant_keeps_trail(self):
        store = EventPositionStore()
        store.add_point("N0DES-7", 39.0, -94.0, kind="other")
        store.add_point("N0DES-7", 39.1, -94.1, kind="participant", callsign="N0DES")
        stations = store.snapshot()["stations"]
        assert len(stations) == 1
        assert stations[0]["kind"] == "participant"
        assert stations[0]["callsign"] == "N0DES"
        assert len(stations[0]["points"]) == 2

    def test_participant_never_demoted(self):
        store = EventPositionStore()
        store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        store.add_point("KE0XYZ-9", 39.1, -94.1, kind="other")
        stations = store.snapshot()["stations"]
        assert len(stations) == 1
        assert stations[0]["kind"] == "participant"

    def test_promoted_station_not_evicted_by_lru(self):
        store = EventPositionStore()
        store.add_point("N0DES-7", 39.0, -94.0, kind="other")
        store.add_point("N0DES-7", 39.1, -94.1, kind="participant", callsign="N0DES")
        for i in range(OTHER_STATIONS_CAP + 5):
            store.add_point(f"X{i}", 39.0, -94.0, kind="other")
        ids = {s["station_id"] for s in store.snapshot()["stations"]}
        assert "N0DES-7" in ids
