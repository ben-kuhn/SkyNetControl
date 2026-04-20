import pytest
from datetime import date, time
from unittest.mock import patch
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
    render_roster,
    generate_draft,
    generate_due_drafts,
    assemble_roster,
    approve_roster,
    mark_sent,
    skip_roster,
    update_draft,
    get_session_geojson,
    notify_ncs,
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


@pytest.fixture
def default_template(db):
    return create_template(
        db,
        name="Default",
        subject_template="Roster — {{ date }}",
        header_template="Session: {{ date }}, NCS: {{ net_control }}, Count: {{ total_count }}",
        welcome_template="{% for m in new_members %}Welcome {{ m.name }} ({{ m.callsign }})!\n{% endfor %}",
        comments_template="{% for c in checkins %}{% if c.comments %}{{ c.callsign }}: {{ c.comments }}\n{% endif %}{% endfor %}",
        footer_template="{% if next_week_preview %}Next: {{ next_week_preview }}{% endif %}\n{% if map_url %}Map: {{ map_url }}{% endif %}\n73 de W0NE",
        lead_time_days=1,
        is_default=True,
    )


# --- Rendering ---

def test_render_roster(db, default_template):
    context = {
        "date": "April 10, 2026", "time": "6:00 PM", "day_of_week": "Thursday",
        "net_control": "W0NE", "activity_title": "", "activity_instructions": "",
        "next_week_preview": "Standard Winlink Check-in", "map_url": "",
        "checkins": [{"name": "Alice", "callsign": "W0TST", "comments": "Hi!",
                      "city": "", "county": "", "state": "", "mode": "winlink",
                      "is_new_member": True}],
        "new_members": [{"name": "Alice", "callsign": "W0TST", "comments": "Hi!",
                         "city": "", "county": "", "state": "", "mode": "winlink",
                         "is_new_member": True}],
        "total_count": 1,
    }
    sections = render_roster(default_template, context)
    assert "April 10, 2026" in sections["subject"]
    assert "W0NE" in sections["header"]
    assert "Welcome Alice" in sections["welcome"]
    assert "W0TST: Hi!" in sections["comments"]
    assert "73 de W0NE" in sections["footer"]


def test_render_roster_jinja_error(db):
    bad_template = create_template(
        db, name="Bad", subject_template="{{ bad syntax {% }}",
        header_template="ok", welcome_template="ok",
        comments_template="ok", footer_template="ok",
    )
    context = {"date": "test"}
    sections = render_roster(bad_template, context)
    assert "Template rendering error" in sections["subject"]
    assert sections["header"] == "ok"


# --- Draft Generation ---

def test_generate_draft(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    assert log is not None
    assert log.status == RosterStatus.DRAFT
    assert "April 10, 2026" in log.content_subject


def test_generate_draft_idempotent(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log1 = generate_draft(db, session1.id)
    log2 = generate_draft(db, session1.id)
    assert log1.id == log2.id


def test_generate_draft_session_not_found(db, default_template):
    assert generate_draft(db, 999) is None


def test_generate_draft_no_default_template(db, season_and_sessions):
    _, session1, _, _ = season_and_sessions
    assert generate_draft(db, session1.id) is None


def test_generate_draft_with_specific_template(db, season_and_sessions):
    _, session1, _, _ = season_and_sessions
    tmpl = create_template(db, name="Custom", subject_template="Custom {{ date }}",
                           header_template="h", welcome_template="w",
                           comments_template="c", footer_template="f")
    log = generate_draft(db, session1.id, template_id=tmpl.id)
    assert "Custom" in log.content_subject


def test_generate_draft_sets_map_url_when_gps(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    ci = CheckIn(session_id=session1.id, callsign="W0GPS", name="GPS Op",
                 mode="winlink", parse_status=ParseStatus.AUTO,
                 timing_status=TimingStatus.ON_TIME, is_new_member=False,
                 latitude=39.7, longitude=-104.9)
    db.add(ci)
    db.commit()
    log = generate_draft(db, session1.id)
    assert log.map_url is not None and log.map_url != ""


def test_generate_due_drafts(db, season_and_sessions, default_template):
    season, session1, _, _ = season_and_sessions
    session1.status = SessionStatus.COMPLETED
    db.commit()

    with patch("backend.modules.roster.service._today", return_value=date(2026, 4, 11)):
        drafts = generate_due_drafts(db)
    assert len(drafts) == 1
    assert drafts[0].session_id == session1.id


def test_generate_due_drafts_skips_no_end_date(db, default_template):
    """Sessions without end_date (real events) are skipped by generate_due_drafts."""
    season = NetSeason(name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                       day_of_week=0)
    db.add(season)
    db.flush()

    session = NetSession(
        season_id=season.id, start_date=date(2026, 4, 5), end_date=None,
        grace_period_hours=24.0, session_type=SessionType.REAL_EVENT,
        status=SessionStatus.COMPLETED,
    )
    db.add(session)
    db.commit()

    with patch("backend.modules.roster.service._today", return_value=date(2026, 4, 10)):
        drafts = generate_due_drafts(db)
    assert len(drafts) == 0


# --- Assembly ---

def test_assemble_roster(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions

    ci = CheckIn(session_id=session1.id, callsign="W0TST", name="Test Op",
                 mode="winlink", parse_status=ParseStatus.AUTO,
                 timing_status=TimingStatus.ON_TIME, is_new_member=True,
                 city="Denver", state="CO", comments="Great net!")
    db.add(ci)
    db.commit()

    log = generate_draft(db, session1.id)
    text = assemble_roster(db, log.id)
    assert text is not None
    assert "Roster" in text
    assert "W0TST" in text
    assert "Test Op" in text
    assert "Welcome Test Op" in text
    assert "Great net!" in text


def test_assemble_roster_not_found(db):
    assert assemble_roster(db, 999) is None


# --- Status Transitions ---

def test_approve_roster(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    result = approve_roster(db, log.id, "W0NE")
    assert result is not None
    assert result.status == RosterStatus.APPROVED
    assert result.approved_by == "W0NE"
    assert result.approved_at is not None
    db.refresh(session1)
    assert session1.status == SessionStatus.COMPLETED


def test_approve_roster_wrong_status(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    approve_roster(db, log.id, "W0NE")
    assert approve_roster(db, log.id, "W0NE") is None


def test_mark_sent(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    approve_roster(db, log.id, "W0NE")
    result = mark_sent(db, log.id)
    assert result is not None
    assert result.status == RosterStatus.SENT
    assert result.sent_at is not None


def test_mark_sent_wrong_status(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    assert mark_sent(db, log.id) is None


def test_skip_roster_from_draft(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    result = skip_roster(db, log.id)
    assert result is not None
    assert result.status == RosterStatus.SKIPPED


def test_skip_roster_from_approved(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    approve_roster(db, log.id, "W0NE")
    result = skip_roster(db, log.id)
    assert result.status == RosterStatus.SKIPPED


def test_skip_sent_roster_fails(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    approve_roster(db, log.id, "W0NE")
    mark_sent(db, log.id)
    assert skip_roster(db, log.id) is None


def test_update_draft(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    result = update_draft(db, log.id, content_header="Edited Header")
    assert result is not None
    assert result.content_header == "Edited Header"


def test_update_draft_non_draft_fails(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    approve_roster(db, log.id, "W0NE")
    assert update_draft(db, log.id, content_header="X") is None


# --- GeoJSON ---

def test_get_session_geojson(db, season_and_sessions):
    _, session1, _, _ = season_and_sessions

    ci1 = CheckIn(session_id=session1.id, callsign="W0GPS", name="GPS Op",
                  mode="winlink", parse_status=ParseStatus.AUTO,
                  timing_status=TimingStatus.ON_TIME, is_new_member=False,
                  latitude=39.7392, longitude=-104.9903)
    ci2 = CheckIn(session_id=session1.id, callsign="W0NOG", name="No GPS",
                  mode="winlink", parse_status=ParseStatus.AUTO,
                  timing_status=TimingStatus.ON_TIME, is_new_member=False)
    db.add_all([ci1, ci2])
    db.commit()

    geojson = get_session_geojson(db, session1.id)
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1
    feat = geojson["features"][0]
    assert feat["geometry"]["type"] == "Point"
    assert feat["geometry"]["coordinates"] == [-104.9903, 39.7392]
    assert feat["properties"]["callsign"] == "W0GPS"


def test_get_session_geojson_empty(db, season_and_sessions):
    _, session1, _, _ = season_and_sessions
    geojson = get_session_geojson(db, session1.id)
    assert geojson["type"] == "FeatureCollection"
    assert geojson["features"] == []


# --- Notify NCS stub ---

def test_notify_ncs_is_noop(db, season_and_sessions):
    _, session1, _, _ = season_and_sessions
    notify_ncs(db, session1)
