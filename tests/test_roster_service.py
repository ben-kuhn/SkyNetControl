import pytest
from datetime import date, time
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.config_mgmt.service import set_config_value
from backend.db.base import Base
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
from backend.modules.activities.models import Activity
from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
from backend.modules.roster.models import RosterStatus
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
    _format_roster_table,
    assemble_roster,
    approve_roster,
    resend_roster,
    mark_sent,
    skip_roster,
    update_draft,
    regenerate_draft,
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
def net_id(db):
    from tests.conftest import make_test_net
    return make_test_net(db).id


@pytest.fixture
def season_and_sessions(db, net_id):
    season = NetSeason(
        net_id=net_id,
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
        net_id=net_id,
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


def test_create_template(db, net_id):
    tmpl = create_template(
        db,
        net_id=net_id,
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


def test_create_template_clears_previous_default(db, net_id):
    t1 = create_template(
        db,
        net_id=net_id,
        name="First",
        subject_template="s",
        header_template="h",
        welcome_template="w",
        comments_template="c",
        footer_template="f",
        is_default=True,
    )
    t2 = create_template(
        db,
        net_id=net_id,
        name="Second",
        subject_template="s",
        header_template="h",
        welcome_template="w",
        comments_template="c",
        footer_template="f",
        is_default=True,
    )
    db.refresh(t1)
    assert t1.is_default is False
    assert t2.is_default is True


def test_get_template(db, net_id):
    tmpl = create_template(
        db,
        net_id=net_id,
        name="T",
        subject_template="s",
        header_template="h",
        welcome_template="w",
        comments_template="c",
        footer_template="f",
    )
    result = get_template(db, tmpl.id)
    assert result is not None
    assert result.name == "T"


def test_get_template_not_found(db):
    assert get_template(db, 999) is None


def test_list_templates(db, net_id):
    create_template(
        db,
        net_id=net_id,
        name="Beta",
        subject_template="s",
        header_template="h",
        welcome_template="w",
        comments_template="c",
        footer_template="f",
    )
    create_template(
        db,
        net_id=net_id,
        name="Alpha",
        subject_template="s",
        header_template="h",
        welcome_template="w",
        comments_template="c",
        footer_template="f",
    )
    result = list_templates(db, net_id=net_id)
    assert len(result) == 2
    assert result[0].name == "Alpha"  # ordered by name


def test_update_template(db, net_id):
    tmpl = create_template(
        db,
        net_id=net_id,
        name="Old",
        subject_template="s",
        header_template="h",
        welcome_template="w",
        comments_template="c",
        footer_template="f",
    )
    updated = update_template(db, tmpl.id, net_id=net_id, name="New", lead_time_days=3)
    assert updated.name == "New"
    assert updated.lead_time_days == 3


def test_update_template_not_found(db, net_id):
    assert update_template(db, 999, net_id=net_id, name="X") is None


def test_delete_template(db, net_id):
    tmpl = create_template(
        db,
        net_id=net_id,
        name="Del",
        subject_template="s",
        header_template="h",
        welcome_template="w",
        comments_template="c",
        footer_template="f",
    )
    assert delete_template(db, tmpl.id, net_id=net_id) is True
    assert get_template(db, tmpl.id) is None


def test_delete_template_blocked_if_default(db, net_id):
    tmpl = create_template(
        db,
        net_id=net_id,
        name="Def",
        subject_template="s",
        header_template="h",
        welcome_template="w",
        comments_template="c",
        footer_template="f",
        is_default=True,
    )
    assert delete_template(db, tmpl.id, net_id=net_id) is False
    assert get_template(db, tmpl.id) is not None


# --- Context Building ---


def test_build_roster_context_regular(db, season_and_sessions):
    season, session1, _, _ = season_and_sessions

    ci1 = CheckIn(
        session_id=session1.id,
        callsign="W0TST",
        name="Test User",
        mode="winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=True,
        city="Denver",
        county="Denver",
        state="CO",
        comments="Hello!",
    )
    ci2 = CheckIn(
        session_id=session1.id,
        callsign="KD0ABC",
        name="Another Op",
        mode="vara",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=False,
        latitude=39.7392,
        longitude=-104.9903,
    )
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


def test_build_roster_context_includes_net_identity(db, season_and_sessions):
    """build_roster_context exposes net_callsign and net_address from config
    so seeded roster templates don't have to hardcode net branding."""
    _, session1, _, _ = season_and_sessions
    set_config_value(db, "default_net_control", "K0XYZ")
    set_config_value(db, "net_address", "k0xyz@winlink.org")

    ctx = build_roster_context(db, session1)

    assert ctx["net_callsign"] == "K0XYZ"
    assert ctx["net_address"] == "k0xyz@winlink.org"


def test_build_roster_context_exposes_start_and_end_date(db, season_and_sessions):
    """Week-long nets need both endpoints in the template context so
    operators can phrase the date span however they like — backlog item 1."""
    _, session1, _, _ = season_and_sessions

    session1.end_date = date(2026, 4, 16)
    db.commit()

    ctx = build_roster_context(db, session1)

    assert ctx["start_date"] == "April 10, 2026"
    assert ctx["end_date"] == "April 16, 2026"
    # `date` stays backward-compatible — same as start_date.
    assert ctx["date"] == "April 10, 2026"


def test_build_roster_context_end_date_falls_back_to_start(db, season_and_sessions):
    """Single-day sessions may have end_date=None; the template var should
    still resolve so {{ end_date }} doesn't render the literal 'None'."""
    _, session1, _, _ = season_and_sessions

    session1.end_date = None
    db.commit()

    ctx = build_roster_context(db, session1)

    assert ctx["start_date"] == "April 10, 2026"
    assert ctx["end_date"] == "April 10, 2026"


def test_build_roster_context_session_url(db, season_and_sessions):
    _, session1, _, _ = season_and_sessions

    ci = CheckIn(
        session_id=session1.id,
        callsign="W0GPS",
        name="GPS Op",
        mode="winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=False,
        latitude=39.7392,
        longitude=-104.9903,
    )
    db.add(ci)
    db.commit()

    ctx = build_roster_context(db, session1)
    assert ctx["session_url"].startswith("http://localhost:8000")
    assert "/checkins?session=" in ctx["session_url"]


@pytest.fixture
def default_template(db, net_id):
    return create_template(
        db,
        net_id=net_id,
        name="Default",
        subject_template="Roster — {{ date }}",
        header_template="Session: {{ date }}, NCS: {{ net_control }}, Count: {{ total_count }}",
        welcome_template="{% for m in new_members %}Welcome {{ m.name }} ({{ m.callsign }})!\n{% endfor %}",
        comments_template="{% for c in checkins %}{% if c.comments %}{{ c.callsign }}: {{ c.comments }}\n{% endif %}{% endfor %}",  # noqa: E501
        footer_template="{% if next_week_preview %}Next: {{ next_week_preview }}{% endif %}\n{% if session_url %}Check-in details: {{ session_url }}{% endif %}\n73 de {{ net_callsign }}",  # noqa: E501
        lead_time_days=1,
        is_default=True,
    )


# --- Rendering ---


def test_render_roster(db, default_template):
    context = {
        "date": "April 10, 2026",
        "time": "6:00 PM",
        "day_of_week": "Thursday",
        "net_control": "W0NE",
        "net_callsign": "W0NE",
        "activity_title": "",
        "activity_instructions": "",
        "next_week_preview": "Standard Winlink Check-in",
        "session_url": "",
        "checkins": [
            {
                "name": "Alice",
                "callsign": "W0TST",
                "comments": "Hi!",
                "city": "",
                "county": "",
                "state": "",
                "mode": "winlink",
                "is_new_member": True,
            }
        ],
        "new_members": [
            {
                "name": "Alice",
                "callsign": "W0TST",
                "comments": "Hi!",
                "city": "",
                "county": "",
                "state": "",
                "mode": "winlink",
                "is_new_member": True,
            }
        ],
        "total_count": 1,
    }
    sections = render_roster(default_template, context)
    assert "April 10, 2026" in sections["subject"]
    assert "W0NE" in sections["header"]
    assert "Welcome Alice" in sections["welcome"]
    assert "W0TST: Hi!" in sections["comments"]
    assert "73 de W0NE" in sections["footer"]


def test_render_roster_jinja_error(db, net_id):
    bad_template = create_template(
        db,
        net_id=net_id,
        name="Bad",
        subject_template="{{ bad syntax {% }}",
        header_template="ok",
        welcome_template="ok",
        comments_template="ok",
        footer_template="ok",
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


def test_generate_draft_with_specific_template(db, net_id, season_and_sessions):
    _, session1, _, _ = season_and_sessions
    tmpl = create_template(
        db,
        net_id=net_id,
        name="Custom",
        subject_template="Custom {{ date }}",
        header_template="h",
        welcome_template="w",
        comments_template="c",
        footer_template="f",
    )
    log = generate_draft(db, session1.id, template_id=tmpl.id)
    assert "Custom" in log.content_subject


def test_generate_draft_sets_session_url(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    assert log is not None
    assert log.session_url is not None
    assert log.session_url != ""
    assert "/checkins?session=" in log.session_url


def test_generate_due_drafts(db, season_and_sessions, default_template):
    season, session1, _, _ = season_and_sessions
    session1.status = SessionStatus.COMPLETED
    db.commit()

    with patch("backend.modules.roster.service._today", return_value=date(2026, 4, 11)):
        drafts = generate_due_drafts(db)
    assert len(drafts) == 1
    assert drafts[0].session_id == session1.id


def test_generate_due_drafts_skips_no_end_date(db, net_id, default_template):
    """Sessions without end_date (real events) are skipped by generate_due_drafts."""
    season = NetSeason(net_id=net_id, name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), day_of_week=0)
    db.add(season)
    db.flush()

    session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 5),
        end_date=None,
        grace_period_hours=24.0,
        session_type=SessionType.REAL_EVENT,
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

    ci = CheckIn(
        session_id=session1.id,
        callsign="W0TST",
        name="Test Op",
        mode="winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=True,
        city="Denver",
        state="CO",
        comments="Great net!",
    )
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


def test_format_roster_table_aligned_columns():
    """Rows share fixed column widths so the roster renders as a real table
    (not the old pipe-separated blob that groups.io flattened into a wall
    of text). New-member marker leads Jake's row; long comments go on
    their own indented line.
    """
    class _CI:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    checkins = [
        _CI(name="Chris", callsign="KD9CZM", city="Holmen", county="La Crosse",
            state="WI", mode="VARA", comments="", is_new_member=False),
        _CI(name="Jake", callsign="KE9FFT", city="La Crosse", county="",
            state="WI", mode="Packet via W9GM-10", comments="", is_new_member=True),
        _CI(name="Kyle", callsign="KF0POS", city="Owatonna", county="Steele",
            state="MN", mode="Packet",
            comments="Good morning! Kyle checking in over packet.",
            is_new_member=False),
    ]

    table = _format_roster_table(checkins)
    lines = table.split("\n")

    callsign_positions = [
        ln.index(cs)
        for ln in lines
        for cs in ("KD9CZM", "KE9FFT", "KF0POS")
        if cs in ln
    ]
    assert len(set(callsign_positions)) == 1, callsign_positions

    # Header + separator + 3 rows + 1 comment sub-line
    assert lines[0].lstrip().startswith("Name")
    jake_line = next(ln for ln in lines if "KE9FFT" in ln)
    assert jake_line.startswith("*"), jake_line
    kyle_line = next(ln for ln in lines if "KF0POS" in ln)
    assert "Good morning" not in kyle_line
    assert any(ln.startswith("    Good morning") for ln in lines)
    assert " | " not in table


def test_format_roster_table_empty():
    assert _format_roster_table([]) == ""


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
    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=True,
    ):
        result = mark_sent(db, log.id)
    assert result is not None
    assert result.status == RosterStatus.SENT
    assert result.sent_at is not None


def test_mark_sent_wrong_status(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    assert mark_sent(db, log.id) is None


def test_resend_roster_requires_sent_status(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    # DRAFT — cannot resend
    assert resend_roster(db, log.id) is None
    approve_roster(db, log.id, "W0NE")
    # APPROVED — still cannot resend
    assert resend_roster(db, log.id) is None


def test_resend_roster_success_refreshes_sent_at(db, season_and_sessions, default_template):
    """resend_roster keeps status=SENT, refreshes sent_at, dispatches delivery."""
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    approve_roster(db, log.id, "W0NE")
    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=True,
    ):
        sent = mark_sent(db, log.id)
    assert sent is not None
    first_sent_at = sent.sent_at

    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=True,
    ) as dispatch:
        resent = resend_roster(db, log.id)
    assert resent is not None
    assert resent.status == RosterStatus.SENT
    assert resent.sent_at is not None and resent.sent_at >= first_sent_at
    # dispatch was called with the roster body — assert content_type=roster.
    args, _ = dispatch.call_args
    assert args[1] == "roster"
    assert args[2] == log.id


def test_resend_roster_delivery_failure_returns_none(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    approve_roster(db, log.id, "W0NE")
    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=True,
    ):
        mark_sent(db, log.id)
    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=False,
    ):
        assert resend_roster(db, log.id) is None
    # Status stays SENT even after failed resend.
    refreshed = db.get(type(log), log.id)
    assert refreshed.status == RosterStatus.SENT


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
    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=True,
    ):
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


# --- Regenerate Draft ---


def test_regenerate_roster_draft_rewrites_all_sections(db, season_and_sessions, default_template):
    """regenerate_draft re-renders all five content fields and session_url."""
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    assert log is not None

    log.content_subject = "Edited subject"
    log.content_header = "Edited header"
    log.content_welcome = "Edited welcome"
    log.content_comments = "Edited comments"
    log.content_footer = "Edited footer"
    log.session_url = "https://stale.example/checkins?session=999"
    db.commit()

    result = regenerate_draft(db, log.id)
    assert result is not None
    assert result.id == log.id
    assert result.content_subject != "Edited subject"
    assert result.content_header != "Edited header"
    assert result.content_welcome != "Edited welcome"
    assert result.content_comments != "Edited comments"
    assert result.content_footer != "Edited footer"
    assert result.session_url is not None
    assert "/checkins?session=" in result.session_url


def test_regenerate_roster_draft_picks_up_new_checkins(db, season_and_sessions, default_template):
    """If check-ins are added after generation, regenerate reflects them."""
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    assert log is not None

    db.add(
        CheckIn(
            session_id=session1.id,
            callsign="W0NEW",
            name="New Person",
            mode="winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
            is_new_member=True,
        )
    )
    db.commit()

    result = regenerate_draft(db, log.id)
    assert result is not None
    combined = result.content_header + result.content_welcome + result.content_comments + result.content_footer
    assert "W0NEW" in combined or "New Person" in combined


def test_regenerate_roster_draft_returns_none_when_not_draft(db, season_and_sessions, default_template):
    """Approved/sent/skipped rosters can't be regenerated."""
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    approve_roster(db, log.id, "W0NE")

    assert regenerate_draft(db, log.id) is None


def test_regenerate_roster_draft_returns_none_when_missing(db):
    assert regenerate_draft(db, 999) is None


def test_generate_due_drafts_creates_notification(db, season_and_sessions, default_template):
    """When generate_due_drafts creates a roster, the session's NCS gets a notification."""
    from datetime import date
    from unittest.mock import patch
    from backend.auth.models import User
    from backend.modules.notifications.models import Notification, NotificationKind
    from backend.modules.roster.service import generate_due_drafts
    from backend.modules.schedule.models import SessionStatus

    _, session1, _, _ = season_and_sessions
    session1.status = SessionStatus.COMPLETED
    db.add(User(callsign="W0NE", oidc_subject="x|w0ne", name="NCS", ))
    db.commit()

    with patch("backend.modules.roster.service._today", return_value=date(2026, 4, 11)):
        generate_due_drafts(db)

    rows = (
        db.query(Notification)
        .filter(
            Notification.recipient_callsign == "W0NE",
            Notification.kind == NotificationKind.ROSTER_DRAFT,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].link_url == "/roster"


# ---------------------------------------------------------------------------
# Source-file purge tests (Task 4)
# ---------------------------------------------------------------------------


def test_mark_sent_purges_session_source_files(db, net_id, tmp_path, monkeypatch):
    """A successful mark_sent deletes the session's PAT mailbox files."""
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from backend.modules.roster.service import mark_sent
    from backend.modules.roster.models import RosterLog, RosterStatus
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=net_id,
        season_id=None,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.REGULAR_CHECKIN,
        grace_period_hours=24,
    )
    db.add(net_session)
    db.commit()
    db.refresh(net_session)

    src = tmp_path / "file.b2f"
    src.write_text("x")
    raw = RawMessage(
        message_id="<id@x>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="s",
        body="b",
        message_type=MessageType.UNKNOWN,
        parsed=True,
        source_path=str(src),
    )
    db.add(raw)
    db.flush()
    db.add(CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="Test",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))

    log = RosterLog(
        session_id=net_session.id,
        status=RosterStatus.APPROVED,
        content_subject="s",
        content_header="h",
        content_welcome="w",
        content_comments="c",
        content_footer="f",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # Stub the delivery dispatcher so the test doesn't try to send email.
    monkeypatch.setattr(
        "backend.integrations.delivery.service.dispatch_delivery",
        lambda *a, **kw: True,
    )

    result = mark_sent(db, log.id)
    assert result is not None
    assert result.status == RosterStatus.SENT
    assert not src.exists(), "source file must be purged after a successful send"


def test_mark_sent_failure_does_not_purge(db, net_id, tmp_path, monkeypatch):
    """A failed delivery (mark_sent returns None) leaves files in place."""
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from backend.modules.roster.service import mark_sent
    from backend.modules.roster.models import RosterLog, RosterStatus
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=net_id,
        season_id=None,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.REGULAR_CHECKIN,
        grace_period_hours=24,
    )
    db.add(net_session)
    db.commit()
    db.refresh(net_session)

    src = tmp_path / "file.b2f"
    src.write_text("x")
    raw = RawMessage(
        message_id="<id@x>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="s",
        body="b",
        message_type=MessageType.UNKNOWN,
        parsed=True,
        source_path=str(src),
    )
    db.add(raw)
    db.flush()
    db.add(CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="Test",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))

    log = RosterLog(
        session_id=net_session.id,
        status=RosterStatus.APPROVED,
        content_subject="s",
        content_header="h",
        content_welcome="w",
        content_comments="c",
        content_footer="f",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    monkeypatch.setattr(
        "backend.integrations.delivery.service.dispatch_delivery",
        lambda *a, **kw: False,
    )

    result = mark_sent(db, log.id)
    assert result is None
    assert src.exists(), "source files must remain when delivery failed"


def test_skip_roster_purges_session_source_files(db, net_id, tmp_path):
    from backend.modules.checkins.models import RawMessage, CheckIn, MessageType, ParseStatus, TimingStatus
    from backend.modules.roster.service import skip_roster
    from backend.modules.roster.models import RosterLog, RosterStatus
    from backend.modules.schedule.models import NetSession, SessionStatus, SessionType
    from datetime import date, datetime, timezone

    net_session = NetSession(
        net_id=net_id,
        season_id=None,
        start_date=date.today(),
        end_date=date.today(),
        status=SessionStatus.SCHEDULED,
        session_type=SessionType.REGULAR_CHECKIN,
        grace_period_hours=24,
    )
    db.add(net_session)
    db.commit()
    db.refresh(net_session)

    src = tmp_path / "file.b2f"
    src.write_text("x")
    raw = RawMessage(
        message_id="<id@x>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="s",
        body="b",
        message_type=MessageType.UNKNOWN,
        parsed=True,
        source_path=str(src),
    )
    db.add(raw)
    db.flush()
    db.add(CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="Test",
        mode="Voice",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    ))

    log = RosterLog(
        session_id=net_session.id,
        status=RosterStatus.DRAFT,
        content_subject="s",
        content_header="h",
        content_welcome="w",
        content_comments="c",
        content_footer="f",
        drafted_at=datetime.now(tz=timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    result = skip_roster(db, log.id)
    assert result is not None
    assert result.status == RosterStatus.SKIPPED
    assert not src.exists()
