import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.activities.models import (
    Activity,
    ChatSession,
    ChatMessage,
    ChatMessageRole,
)
import backend.modules.schedule.models  # noqa: F401 - needed for FK


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_chat_session(db: Session):
    chat = ChatSession()
    db.add(chat)
    db.commit()

    fetched = db.get(ChatSession, chat.id)
    assert fetched is not None
    assert fetched.activity_id is None
    assert fetched.created_at is not None


def test_chat_messages(db: Session):
    chat = ChatSession()
    db.add(chat)
    db.commit()

    msg1 = ChatMessage(
        chat_session_id=chat.id,
        role=ChatMessageRole.USER,
        content="I want an activity about emergency prep",
    )
    msg2 = ChatMessage(
        chat_session_id=chat.id,
        role=ChatMessageRole.ASSISTANT,
        content="How about a simulated emergency net exercise?",
    )
    db.add_all([msg1, msg2])
    db.commit()

    db.refresh(chat)
    assert len(chat.messages) == 2
    assert chat.messages[0].role == ChatMessageRole.USER
    assert chat.messages[1].role == ChatMessageRole.ASSISTANT


def test_link_chat_to_activity(db: Session):
    activity = Activity(
        title="Emergency Prep",
        description="Practice emergency communications",
        instructions="Set up go-kit and check in",
    )
    db.add(activity)
    db.commit()

    chat = ChatSession(activity_id=activity.id)
    db.add(chat)
    db.commit()

    db.refresh(chat)
    assert chat.activity is not None
    assert chat.activity.title == "Emergency Prep"

    db.refresh(activity)
    assert len(activity.chat_sessions) == 1
