from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.modules.events.service as events_service
from backend.db.base import Base
from backend.modules.events.models import (
    EventLogEntry,
    EventLogType,
    EventType,
    ParticipantStatus,
)
from backend.modules.events.service import (
    DuplicateParticipantError,
    EventNotActiveError,
    InvalidPostError,
    InvalidStatusTransitionError,
    activate_event,
    add_note,
    check_in,
    close_event,
    compute_report,
    create_event,
    create_post,
    set_log_pinned,
    update_participant,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def event(db):
    e = create_event(
        db, name="Tornado Watch", event_type=EventType.EMERGENCY, created_by="W0NE"
    )
    activate_event(db, e.id, actor="W0NE")
    db.refresh(e)
    return e


@pytest.fixture(autouse=True)
def _no_callbook(monkeypatch):
    """Default: callbook returns nothing. Tests that exercise prefill override."""
    monkeypatch.setattr(events_service, "lookup_callsign", lambda db, cs: None)


def _last_log(db, event_id) -> EventLogEntry:
    return (
        db.query(EventLogEntry)
        .filter(EventLogEntry.event_id == event_id)
        .order_by(EventLogEntry.seq.desc())
        .first()
    )


class TestCheckIn:
    def test_check_in_uppercases_and_logs(self, db, event):
        p = check_in(db, event.id, callsign="ke0xyz", actor="W0NC")
        assert p.callsign == "KE0XYZ"
        assert p.current_status == ParticipantStatus.CHECKED_IN
        entry = _last_log(db, event.id)
        assert entry.entry_type == EventLogType.SYSTEM
        assert entry.callsign == "KE0XYZ"
        assert entry.new_status == ParticipantStatus.CHECKED_IN
        assert entry.actor == "W0NC"

    def test_callbook_prefill(self, db, event, monkeypatch):
        monkeypatch.setattr(
            events_service, "lookup_callsign",
            lambda db, cs: {"callsign": cs, "name": "Jane Doe"},
        )
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        assert p.name == "Jane Doe"

    def test_explicit_name_skips_callbook(self, db, event, monkeypatch):
        def boom(db, cs):
            raise AssertionError("should not be called")
        monkeypatch.setattr(events_service, "lookup_callsign", boom)
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC", name="Bob")
        assert p.name == "Bob"

    def test_callbook_failure_is_nonfatal(self, db, event, monkeypatch):
        def boom(db, cs):
            raise RuntimeError("provider down")
        monkeypatch.setattr(events_service, "lookup_callsign", boom)
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        assert p.name is None

    def test_duplicate_active_check_in_raises(self, db, event):
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        with pytest.raises(DuplicateParticipantError):
            check_in(db, event.id, callsign="ke0xyz", actor="W0NC")

    def test_recheck_in_after_checkout_reuses_row(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)
        p2 = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        assert p2.id == p.id
        assert p2.current_status == ParticipantStatus.CHECKED_IN
        assert p2.checked_out_at is None

    def test_check_in_with_post(self, db, event):
        post = create_post(db, event.id, name="EOC")
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC", post_id=post.id)
        assert p.post_id == post.id
        assert "EOC" in _last_log(db, event.id).message

    def test_check_in_with_foreign_post_raises(self, db, event):
        other = create_event(
            db, name="Other", event_type=EventType.EMERGENCY, created_by="W0NE"
        )
        activate_event(db, other.id, actor="W0NE")
        foreign_post = create_post(db, other.id, name="Elsewhere")
        with pytest.raises(InvalidPostError):
            check_in(db, event.id, callsign="KE0XYZ", actor="W0NC", post_id=foreign_post.id)

    def test_check_in_on_inactive_event_raises(self, db):
        draft = create_event(
            db, name="D", event_type=EventType.EMERGENCY, created_by="W0NE"
        )
        with pytest.raises(EventNotActiveError):
            check_in(db, draft.id, callsign="KE0XYZ", actor="W0NC")


class TestStatusMachine:
    def test_valid_transitions(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        for status in (
            ParticipantStatus.EN_ROUTE,
            ParticipantStatus.AT_POST,
            ParticipantStatus.OUT_OF_SERVICE,
            ParticipantStatus.AT_POST,
            ParticipantStatus.CHECKED_OUT,
        ):
            p = update_participant(db, event.id, p.id, actor="W0NC", status=status)
            assert p.current_status == status
        assert p.checked_out_at is not None

    def test_checked_out_only_returns_via_checked_in(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)
        with pytest.raises(InvalidStatusTransitionError):
            update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.AT_POST)
        p = update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_IN)
        assert p.current_status == ParticipantStatus.CHECKED_IN
        assert p.checked_out_at is None

    def test_status_change_logs_with_new_status(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.EN_ROUTE)
        entry = _last_log(db, event.id)
        assert entry.new_status == ParticipantStatus.EN_ROUTE
        assert entry.callsign == "KE0XYZ"

    def test_same_status_is_noop(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        seq_before = _last_log(db, event.id).seq
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_IN)
        assert _last_log(db, event.id).seq == seq_before

    def test_location_and_post_changes_log(self, db, event):
        post = create_post(db, event.id, name="Shelter A")
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", location="Mobile, Hwy 9")
        assert "Hwy 9" in _last_log(db, event.id).message
        update_participant(db, event.id, p.id, actor="W0NC", post_id=post.id)
        assert "Shelter A" in _last_log(db, event.id).message

    def test_name_change_does_not_log(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        seq_before = _last_log(db, event.id).seq
        update_participant(db, event.id, p.id, actor="W0NC", name="Corrected Name")
        assert _last_log(db, event.id).seq == seq_before

    def test_update_on_closed_event_raises(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)


class TestNotes:
    def test_event_note(self, db, event):
        entry = add_note(db, event.id, actor="W0NC", message="Course clear")
        assert entry.entry_type == EventLogType.NOTE
        assert entry.callsign is None

    def test_participant_note(self, db, event):
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        entry = add_note(db, event.id, actor="W0NC", message="Has medical training",
                         callsign="ke0xyz", pinned=True)
        assert entry.entry_type == EventLogType.PARTICIPANT_NOTE
        assert entry.callsign == "KE0XYZ"
        assert entry.pinned is True

    def test_note_on_closed_event_raises(self, db, event):
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            add_note(db, event.id, actor="W0NC", message="too late")

    def test_pin_unpin(self, db, event):
        entry = add_note(db, event.id, actor="W0NC", message="x")
        result = set_log_pinned(db, event.id, entry.id, True)
        assert result.pinned is True
        result = set_log_pinned(db, event.id, entry.id, False)
        assert result.pinned is False

    def test_pin_missing_entry_returns_none(self, db, event):
        assert set_log_pinned(db, event.id, 9999, True) is None

    def test_set_log_pinned_on_closed_event_raises(self, db, event):
        entry = add_note(db, event.id, actor="W0NC", message="note")
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            set_log_pinned(db, event.id, entry.id, True)


class TestReport:
    def test_two_stints_hours(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)
        db.refresh(event)
        report = compute_report(db, event)
        assert len(report) == 1
        entry = report[0]
        assert entry["callsign"] == "KE0XYZ"
        assert len(entry["stints"]) == 2
        assert all(s["end"] is not None for s in entry["stints"])
        assert entry["total_seconds"] >= 0

    def test_open_stint_ends_at_close(self, db, event):
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        close_event(db, event.id, actor="W0NE")
        db.refresh(event)
        report = compute_report(db, event)
        stint = report[0]["stints"][0]
        assert stint["end"] is None  # still open in the payload
        closed_at = event.closed_at.replace(tzinfo=timezone.utc)
        start = datetime.fromisoformat(stint["start"])
        expected = int((closed_at - start.replace(tzinfo=timezone.utc)).total_seconds())
        assert abs(report[0]["total_seconds"] - expected) <= 1
