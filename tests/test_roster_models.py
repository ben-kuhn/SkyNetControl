import pytest
from datetime import date, datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.modules.schedule.models import NetSeason, NetSession, SessionType
from backend.modules.roster.models import RosterTemplate, RosterLog, RosterStatus


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


def test_create_roster_template(db):
    template = RosterTemplate(
        name="Default Roster",
        subject_template="{{ net_callsign }} Roster — {{ date }}",
        header_template="Session on {{ date }}.",
        welcome_template="{% for m in new_members %}Welcome {{ m.name }}!{% endfor %}",
        comments_template="{% for c in checkins %}{% if c.comments %}{{ c.callsign }}: {{ c.comments }}{% endif %}{% endfor %}",  # noqa: E501
        footer_template="Next week: {{ next_week_preview }}",
        lead_time_days=1,
        is_default=True,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    assert template.id is not None
    assert template.name == "Default Roster"
    assert template.is_default is True
    assert template.lead_time_days == 1


def test_create_roster_log(db, net_id):
    season = NetSeason(
        net_id=net_id,
        name="Spring 2026",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 6, 30),
        day_of_week=3,
        time=None,
    )
    db.add(season)
    db.flush()

    session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(session)
    db.flush()

    template = RosterTemplate(
        name="Default",
        subject_template="subj",
        header_template="hdr",
        welcome_template="welcome",
        comments_template="comments",
        footer_template="footer",
        is_default=True,
    )
    db.add(template)
    db.flush()

    log = RosterLog(
        session_id=session.id,
        template_id=template.id,
        status=RosterStatus.DRAFT,
        content_subject="Subject Line",
        content_header="Header Text",
        content_welcome="Welcome Text",
        content_comments="Comments Text",
        content_footer="Footer Text",
        drafted_at=datetime.now(tz=timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    assert log.id is not None
    assert log.status == RosterStatus.DRAFT
    assert log.approved_at is None
    assert log.approved_by is None
    assert log.sent_at is None
    assert log.session_url is None


def test_roster_status_values():
    assert RosterStatus.DRAFT.value == "draft"
    assert RosterStatus.APPROVED.value == "approved"
    assert RosterStatus.SENT.value == "sent"
    assert RosterStatus.SKIPPED.value == "skipped"
