import pytest
from datetime import date, time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
import backend.modules.schedule.models
import backend.modules.activities.models
import backend.modules.checkins.models
import backend.modules.roster.models
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
from backend.modules.activities.models import Activity
from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
from backend.modules.roster.models import RosterTemplate, RosterLog, RosterStatus
from backend.modules.roster.service import (
    create_template,
    get_template,
    list_templates,
    update_template,
    delete_template,
    build_roster_context,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def season_and_sessions(db):
    season = NetSeason(
        name="Spring 2026",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 6, 30),
        day_of_week=3,  # Thursday
        time=time(18, 0),
    )
    db.add(season)
    db.flush()

    session1 = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
        net_control_callsign="W0NE",
    )
    db.add(session1)
    db.flush()

    activity = Activity(
        title="Simplex Exercise",
        description="Local simplex drill",
        instructions="Tune to 146.520 and call CQ.",
    )
    db.add(activity)
    db.flush()

    session2 = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 17),
        end_date=date(2026, 4, 17),
        grace_period_hours=24.0,
        session_type=SessionType.ACTIVITY,
        net_control_callsign="W0NC",
        activity_id=activity.id,
    )
    db.add(session2)
    db.flush()

    return season, session1, session2, activity


# --- Template CRUD ---

def test_create_template(db):
    tmpl = create_template(
        db,
        name="Test Roster",
        subject_template="Subj {{ date }}",
        header_template="Header",
        welcome_template="Welcome",
        comments_template="Comments",
        footer_template="Footer",
        lead_time_days=1,
        is_default=True,
    )
    assert tmpl.id is not None
    assert tmpl.name == "Test Roster"
    assert tmpl.is_default is True


def test_create_template_clears_previous_default(db):
    t1 = create_template(db, name="First", subject_template="s", header_template="h",
                         welcome_template="w", comments_template="c", footer_template="f",
                         is_default=True)
    t2 = create_template(db, name="Second", subject_template="s", header_template="h",
                         welcome_template="w", comments_template="c", footer_template="f",
                         is_default=True)
    db.refresh(t1)
    assert t1.is_default is False
    assert t2.is_default is True


def test_get_template(db):
    tmpl = create_template(db, name="T", subject_template="s", header_template="h",
                           welcome_template="w", comments_template="c", footer_template="f")
    result = get_template(db, tmpl.id)
    assert result is not None
    assert result.name == "T"


def test_get_template_not_found(db):
    assert get_template(db, 999) is None


def test_list_templates(db):
    create_template(db, name="Beta", subject_template="s", header_template="h",
                    welcome_template="w", comments_template="c", footer_template="f")
    create_template(db, name="Alpha", subject_template="s", header_template="h",
                    welcome_template="w", comments_template="c", footer_template="f")
    result = list_templates(db)
    assert len(result) == 2
    assert result[0].name == "Alpha"  # ordered by name


def test_update_template(db):
    tmpl = create_template(db, name="Old", subject_template="s", header_template="h",
                           welcome_template="w", comments_template="c", footer_template="f")
    updated = update_template(db, tmpl.id, name="New", lead_time_days=3)
    assert updated.name == "New"
    assert updated.lead_time_days == 3


def test_update_template_not_found(db):
    assert update_template(db, 999, name="X") is None


def test_delete_template(db):
    tmpl = create_template(db, name="Del", subject_template="s", header_template="h",
                           welcome_template="w", comments_template="c", footer_template="f")
    assert delete_template(db, tmpl.id) is True
    assert get_template(db, tmpl.id) is None


def test_delete_template_blocked_if_default(db):
    tmpl = create_template(db, name="Def", subject_template="s", header_template="h",
                           welcome_template="w", comments_template="c", footer_template="f",
                           is_default=True)
    assert delete_template(db, tmpl.id) is False
    assert get_template(db, tmpl.id) is not None


# --- Context Building ---

def test_build_roster_context_regular(db, season_and_sessions):
    season, session1, _, _ = season_and_sessions

    ci1 = CheckIn(session_id=session1.id, callsign="W0TST", name="Test User",
                  mode="winlink", parse_status=ParseStatus.AUTO,
                  timing_status=TimingStatus.ON_TIME, is_new_member=True,
                  city="Denver", county="Denver", state="CO",
                  comments="Hello!")
    ci2 = CheckIn(session_id=session1.id, callsign="KD0ABC", name="Another Op",
                  mode="vara", parse_status=ParseStatus.AUTO,
                  timing_status=TimingStatus.ON_TIME, is_new_member=False,
                  latitude=39.7392, longitude=-104.9903)
    db.add_all([ci1, ci2])
    db.commit()

    ctx = build_roster_context(db, session1)

    assert ctx["date"] == "April 10, 2026"
    assert ctx["time"] == "6:00 PM"
    assert ctx["day_of_week"] == "Thursday"
    assert ctx["net_control"] == "W0NE"
    assert ctx["total_count"] == 2
    assert len(ctx["checkins"]) == 2
    # Ordered by name alphabetically
    assert ctx["checkins"][0]["name"] == "Another Op"
    assert ctx["checkins"][1]["name"] == "Test User"
    assert len(ctx["new_members"]) == 1
    assert ctx["new_members"][0]["callsign"] == "W0TST"
    assert ctx["activity_title"] == ""


def test_build_roster_context_activity(db, season_and_sessions):
    _, _, session2, activity = season_and_sessions

    ctx = build_roster_context(db, session2)
    assert ctx["activity_title"] == "Simplex Exercise"
    assert ctx["activity_instructions"] == "Tune to 146.520 and call CQ."
    assert ctx["total_count"] == 0
    assert ctx["checkins"] == []
    assert ctx["new_members"] == []


def test_build_roster_context_map_url(db, season_and_sessions):
    _, session1, _, _ = season_and_sessions

    ci = CheckIn(session_id=session1.id, callsign="W0GPS", name="GPS Op",
                 mode="winlink", parse_status=ParseStatus.AUTO,
                 timing_status=TimingStatus.ON_TIME, is_new_member=False,
                 latitude=39.7392, longitude=-104.9903)
    db.add(ci)
    db.commit()

    ctx = build_roster_context(db, session1)
    assert ctx["map_url"] != ""
