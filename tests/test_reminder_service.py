import pytest
from datetime import date, time
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
import backend.modules.schedule.models
import backend.modules.activities.models
import backend.modules.reminders.models
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
from backend.modules.activities.models import Activity
from backend.modules.reminders.models import ReminderTemplate, TemplateType, ReminderLog, ReminderStatus
from backend.modules.reminders.service import (
    create_template,
    get_template,
    list_templates,
    update_template,
    delete_template,
    build_template_context,
    render_reminder,
    generate_draft,
    generate_due_drafts,
    approve_reminder,
    mark_sent,
    skip_reminder,
    update_draft,
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
        time=time(18, 0),  # 6:00 PM
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
        description="Local simplex communication exercise",
        instructions="Tune to 146.520 MHz and call CQ.",
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
    db.commit()

    return season, session1, session2, activity


# --- Template CRUD tests ---

def test_create_template(db: Session):
    tmpl = create_template(
        db,
        name="Regular Reminder",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net Reminder",
        body_template="Don't forget to check in!",
    )
    assert tmpl.id is not None
    assert tmpl.name == "Regular Reminder"
    assert tmpl.template_type == TemplateType.REGULAR_CHECKIN
    assert tmpl.lead_time_days == 2
    assert tmpl.is_default is False


def test_create_default_template_clears_previous(db: Session):
    first = create_template(
        db,
        name="First Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Subject",
        body_template="Body",
        is_default=True,
    )
    assert first.is_default is True

    second = create_template(
        db,
        name="Second Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Subject 2",
        body_template="Body 2",
        is_default=True,
    )
    db.refresh(first)
    assert first.is_default is False
    assert second.is_default is True


def test_list_templates(db: Session):
    create_template(db, name="Zeta", template_type=TemplateType.ACTIVITY,
                    subject_template="S", body_template="B")
    create_template(db, name="Alpha", template_type=TemplateType.REGULAR_CHECKIN,
                    subject_template="S", body_template="B")
    create_template(db, name="Mu", template_type=TemplateType.REGULAR_CHECKIN,
                    subject_template="S", body_template="B")

    templates = list_templates(db)
    names = [t.name for t in templates]
    assert names == sorted(names)
    assert len(templates) == 3


def test_get_template(db: Session):
    tmpl = create_template(
        db, name="Fetch Me", template_type=TemplateType.ACTIVITY,
        subject_template="S", body_template="B"
    )
    fetched = get_template(db, tmpl.id)
    assert fetched is not None
    assert fetched.id == tmpl.id

    missing = get_template(db, 99999)
    assert missing is None


def test_update_template(db: Session):
    tmpl = create_template(
        db, name="Original Name", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Old Subject", body_template="Old Body"
    )
    updated = update_template(db, tmpl.id, name="Updated Name", body_template="New Body")
    assert updated is not None
    assert updated.name == "Updated Name"
    assert updated.body_template == "New Body"
    assert updated.subject_template == "Old Subject"


def test_update_template_sets_default(db: Session):
    first = create_template(
        db, name="First", template_type=TemplateType.ACTIVITY,
        subject_template="S", body_template="B", is_default=True
    )
    second = create_template(
        db, name="Second", template_type=TemplateType.ACTIVITY,
        subject_template="S", body_template="B"
    )
    update_template(db, second.id, is_default=True)
    db.refresh(first)
    db.refresh(second)
    assert first.is_default is False
    assert second.is_default is True


def test_delete_template(db: Session):
    tmpl = create_template(
        db, name="To Delete", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="S", body_template="B"
    )
    result = delete_template(db, tmpl.id)
    assert result is True
    assert get_template(db, tmpl.id) is None


def test_cannot_delete_default_template(db: Session):
    tmpl = create_template(
        db, name="Default Template", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="S", body_template="B", is_default=True
    )
    result = delete_template(db, tmpl.id)
    assert result is False
    assert get_template(db, tmpl.id) is not None


# --- Context building tests ---

def test_build_template_context_regular(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    ctx = build_template_context(db, session1)

    assert ctx["date"] == "April 10, 2026"
    assert ctx["time"] == "6:00 PM"
    assert ctx["day_of_week"] == "Thursday"
    assert ctx["net_control"] == "W0NE"
    assert ctx["activity_title"] == ""
    assert ctx["activity_instructions"] == ""
    assert "Simplex Exercise" in ctx["next_week_preview"]


def test_build_template_context_activity(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    ctx = build_template_context(db, session2)

    assert ctx["date"] == "April 17, 2026"
    assert ctx["activity_title"] == "Simplex Exercise"
    assert "146.520" in ctx["activity_instructions"]
    assert ctx["net_control"] == "W0NC"


def test_build_template_context_no_next_session(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    # session2 is the last session, so no next session
    ctx = build_template_context(db, session2)
    assert ctx["next_week_preview"] == ""


# --- Rendering tests ---

def test_render_reminder(db: Session):
    template = ReminderTemplate(
        name="Basic Template",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Hello {{ net_control }}, the net is on {{ date }} at {{ time }}.",
        lead_time_days=2,
        is_default=False,
    )
    context = {
        "date": "April 10, 2026",
        "time": "6:00 PM",
        "net_control": "W0NE",
    }
    subject, body = render_reminder(template, context)
    assert subject == "Net on April 10, 2026"
    assert "W0NE" in body
    assert "April 10, 2026" in body
    assert "6:00 PM" in body


def test_render_reminder_with_jinja2_conditional(db: Session):
    template = ReminderTemplate(
        name="Conditional Template",
        template_type=TemplateType.ACTIVITY,
        subject_template="Upcoming Net",
        body_template="{% if activity_title %}Activity: {{ activity_title }}{% else %}Regular check-in{% endif %}",
        lead_time_days=2,
        is_default=False,
    )
    context_with_activity = {"activity_title": "Simplex Exercise"}
    _, body = render_reminder(template, context_with_activity)
    assert "Activity: Simplex Exercise" in body

    context_without = {"activity_title": ""}
    _, body2 = render_reminder(template, context_without)
    assert "Regular check-in" in body2


def test_render_reminder_bad_syntax(db: Session):
    template = ReminderTemplate(
        name="Bad Template",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Subject",
        body_template="{% for x in %}broken{%endfor%}",
        lead_time_days=2,
        is_default=False,
    )
    _, body = render_reminder(template, {})
    assert "error" in body.lower()


# --- Draft generation and status transition tests ---

def test_generate_draft(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    tmpl = create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }} at {{ time }}.",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None
    assert log.template_id == tmpl.id
    assert log.status == ReminderStatus.DRAFT
    assert log.drafted_at is not None
    assert "April 10, 2026" in log.content_subject
    assert "April 10, 2026" in log.content_body


def test_generate_draft_is_idempotent(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    log1 = generate_draft(db, session1.id)
    log2 = generate_draft(db, session1.id)
    assert log1 is not None
    assert log2 is not None
    assert log1.id == log2.id


def test_generate_draft_with_explicit_template(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    # Create a default template and a separate explicit one
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Default Subject",
        body_template="Default body.",
        lead_time_days=3,
        is_default=True,
    )
    explicit_tmpl = create_template(
        db,
        name="Explicit Template",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Explicit: {{ date }}",
        body_template="Explicit body for {{ date }}.",
        lead_time_days=3,
        is_default=False,
    )
    log = generate_draft(db, session1.id, template_id=explicit_tmpl.id)
    assert log is not None
    assert log.template_id == explicit_tmpl.id
    assert "Explicit" in log.content_subject


def test_generate_due_drafts(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    # session1 is April 10, session2 is April 17
    # With today=April 8, lead_time=3: session1 (2 days away) is due, session2 (9 days away) is not
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    create_template(
        db,
        name="Activity Default",
        template_type=TemplateType.ACTIVITY,
        subject_template="Activity: {{ activity_title }}",
        body_template="Join us for {{ activity_title }}.",
        lead_time_days=3,
        is_default=True,
    )
    with patch("backend.modules.reminders.service._today", return_value=date(2026, 4, 8)):
        drafts = generate_due_drafts(db)

    assert len(drafts) == 1
    assert drafts[0].session_id == session1.id


def test_generate_due_drafts_skips_completed_sessions(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    # Mark session1 as completed
    session1.status = SessionStatus.COMPLETED
    db.commit()

    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    with patch("backend.modules.reminders.service._today", return_value=date(2026, 4, 8)):
        drafts = generate_due_drafts(db)

    session_ids = [d.session_id for d in drafts]
    assert session1.id not in session_ids


def test_approve_reminder(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None

    approved = approve_reminder(db, log.id, approver_callsign="W0NE")
    assert approved is not None
    assert approved.status == ReminderStatus.APPROVED
    assert approved.approved_by == "W0NE"
    assert approved.approved_at is not None


def test_approve_non_draft_returns_none(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None
    approve_reminder(db, log.id, approver_callsign="W0NE")

    # Trying to approve again should return None
    result = approve_reminder(db, log.id, approver_callsign="W0NE")
    assert result is None


def test_mark_sent(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None
    approve_reminder(db, log.id, approver_callsign="W0NE")

    sent = mark_sent(db, log.id)
    assert sent is not None
    assert sent.status == ReminderStatus.SENT
    assert sent.sent_at is not None


def test_mark_sent_non_approved_returns_none(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None

    # Trying to mark as sent while still DRAFT should return None
    result = mark_sent(db, log.id)
    assert result is None


def test_skip_reminder_from_draft(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None

    skipped = skip_reminder(db, log.id)
    assert skipped is not None
    assert skipped.status == ReminderStatus.SKIPPED


def test_skip_reminder_from_approved(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None
    approve_reminder(db, log.id, approver_callsign="W0NE")

    skipped = skip_reminder(db, log.id)
    assert skipped is not None
    assert skipped.status == ReminderStatus.SKIPPED


def test_update_draft(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None

    updated = update_draft(db, log.id, content_subject="New Subject", content_body="New Body")
    assert updated is not None
    assert updated.content_subject == "New Subject"
    assert updated.content_body == "New Body"
    assert updated.status == ReminderStatus.DRAFT


def test_update_draft_non_draft_returns_none(db: Session, season_and_sessions):
    season, session1, session2, activity = season_and_sessions
    create_template(
        db,
        name="Regular Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Check-in on {{ date }}.",
        lead_time_days=3,
        is_default=True,
    )
    log = generate_draft(db, session1.id)
    assert log is not None
    approve_reminder(db, log.id, approver_callsign="W0NE")

    # Cannot edit an approved reminder
    result = update_draft(db, log.id, content_subject="Should Fail")
    assert result is None
