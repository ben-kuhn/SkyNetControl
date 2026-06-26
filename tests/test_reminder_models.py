import pytest
from datetime import date, datetime, time, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
)
from backend.modules.reminders.models import (
    TemplateType,
    ReminderStatus,
    ReminderTemplate,
    ReminderLog,
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
def season_and_session(db, net_id):
    season = NetSeason(
        net_id=net_id,
        name="Test Season",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 6, 30),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season)
    db.flush()

    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session)
    db.commit()
    return season, net_session


def test_create_reminder_template(db: Session, net_id):
    template = ReminderTemplate(
        net_id=net_id,
        name="Regular Check-in Reminder",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Reminder: Weekly Net Check-in",
        body_template="Please check in to the net.",
        lead_time_days=2,
        is_default=True,
    )
    db.add(template)
    db.commit()

    fetched = db.get(ReminderTemplate, template.id)
    assert fetched is not None
    assert fetched.net_id == net_id
    assert fetched.name == "Regular Check-in Reminder"
    assert fetched.template_type == TemplateType.REGULAR_CHECKIN
    assert fetched.subject_template == "Reminder: Weekly Net Check-in"
    assert fetched.body_template == "Please check in to the net."
    assert fetched.lead_time_days == 2
    assert fetched.is_default is True


def test_template_name_unique_per_net(db: Session, net_id):
    """Same name in the same net is rejected; same name in different nets is OK."""
    from tests.conftest import make_test_net

    template1 = ReminderTemplate(
        net_id=net_id,
        name="Unique Name",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Subject 1",
        body_template="Body 1",
        lead_time_days=2,
        is_default=False,
    )
    db.add(template1)
    db.commit()

    # Same name, same net → should fail
    template2 = ReminderTemplate(
        net_id=net_id,
        name="Unique Name",
        template_type=TemplateType.ACTIVITY,
        subject_template="Subject 2",
        body_template="Body 2",
        lead_time_days=3,
        is_default=False,
    )
    db.add(template2)

    with pytest.raises(Exception):  # IntegrityError from SQLAlchemy
        db.commit()


def test_template_name_allowed_in_different_nets(db: Session, net_id):
    """Same template name is allowed across different nets."""
    from tests.conftest import make_test_net

    net2 = make_test_net(db, slug="net2", name="Net 2")

    t1 = ReminderTemplate(
        net_id=net_id,
        name="Shared Name",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="S1",
        body_template="B1",
        lead_time_days=2,
        is_default=False,
    )
    t2 = ReminderTemplate(
        net_id=net2.id,
        name="Shared Name",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="S2",
        body_template="B2",
        lead_time_days=2,
        is_default=False,
    )
    db.add_all([t1, t2])
    db.commit()  # should not raise

    assert t1.id is not None
    assert t2.id is not None


def test_create_reminder_log(db: Session, season_and_session, net_id):
    _, net_session = season_and_session

    template = ReminderTemplate(
        net_id=net_id,
        name="Test Template",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Subject",
        body_template="Body",
        lead_time_days=2,
        is_default=False,
    )
    db.add(template)
    db.commit()

    reminder_log = ReminderLog(
        session_id=net_session.id,
        template_id=template.id,
        status=ReminderStatus.DRAFT,
        content_subject="Test Subject",
        content_body="Test Body",
        drafted_at=datetime.now(timezone.utc),
    )
    db.add(reminder_log)
    db.commit()

    fetched = db.get(ReminderLog, reminder_log.id)
    assert fetched is not None
    assert fetched.session_id == net_session.id
    assert fetched.template_id == template.id
    assert fetched.status == ReminderStatus.DRAFT
    assert fetched.content_subject == "Test Subject"
    assert fetched.content_body == "Test Body"
    assert fetched.approved_at is None
    assert fetched.sent_at is None
    assert fetched.approved_by is None


def test_reminder_log_session_id_is_unique(db: Session, season_and_session):
    _, net_session = season_and_session

    reminder_log1 = ReminderLog(
        session_id=net_session.id,
        template_id=None,
        status=ReminderStatus.DRAFT,
        content_subject="Subject 1",
        content_body="Body 1",
        drafted_at=datetime.now(timezone.utc),
    )
    db.add(reminder_log1)
    db.commit()

    reminder_log2 = ReminderLog(
        session_id=net_session.id,
        template_id=None,
        status=ReminderStatus.DRAFT,
        content_subject="Subject 2",
        content_body="Body 2",
        drafted_at=datetime.now(timezone.utc),
    )
    db.add(reminder_log2)

    with pytest.raises(Exception):  # IntegrityError from SQLAlchemy
        db.commit()


def test_reminder_log_relationships(db: Session, season_and_session, net_id):
    season, net_session = season_and_session

    template = ReminderTemplate(
        net_id=net_id,
        name="Template for Relationship Test",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Subject",
        body_template="Body",
        lead_time_days=2,
        is_default=False,
    )
    db.add(template)
    db.commit()

    reminder_log = ReminderLog(
        session_id=net_session.id,
        template_id=template.id,
        status=ReminderStatus.DRAFT,
        content_subject="Test Subject",
        content_body="Test Body",
        drafted_at=datetime.now(timezone.utc),
    )
    db.add(reminder_log)
    db.commit()

    db.refresh(reminder_log)
    assert reminder_log.session is not None
    assert reminder_log.session.id == net_session.id
    assert reminder_log.template is not None
    assert reminder_log.template.id == template.id
    assert reminder_log.template.name == "Template for Relationship Test"
