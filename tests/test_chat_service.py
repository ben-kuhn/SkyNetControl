import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.activities.models import (
    Activity,
    ChatSession,
    ChatMessage,
    ChatMessageRole,
)
from backend.modules.activities.chat_service import (
    create_chat_session,
    send_message,
    link_chat_to_activity,
    get_chat_session,
    get_chat_history,
)
import backend.modules.schedule.models  # noqa: F401


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_chat_session(db: Session):
    chat = create_chat_session(db)
    assert chat.id is not None
    assert chat.activity_id is None
    assert len(chat.messages) == 0


def test_send_message_stores_user_message(db: Session):
    chat = create_chat_session(db)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Here's an activity idea: Emergency Net Drill")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        user_msg, assistant_msg = send_message(
            db, chat.id, "I want an emergency prep activity", api_key="test-key"
        )

    assert user_msg.role == ChatMessageRole.USER
    assert user_msg.content == "I want an emergency prep activity"
    assert assistant_msg.role == ChatMessageRole.ASSISTANT
    assert "Emergency Net Drill" in assistant_msg.content


def test_send_message_passes_history(db: Session):
    chat = create_chat_session(db)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Response 1")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        send_message(db, chat.id, "First message", api_key="test-key")

    mock_response2 = MagicMock()
    mock_response2.content = [MagicMock(text="Response 2")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response2
        send_message(db, chat.id, "Second message", api_key="test-key")

        # Verify Claude was called with full history (2 user msgs + 1 assistant msg)
        call_args = mock_claude.call_args
        messages = call_args[1]["messages"]
        assert len(messages) == 3  # user, assistant, user


def test_link_chat_to_activity(db: Session):
    chat = create_chat_session(db)
    activity = Activity(
        title="Linked Activity",
        description="d",
        instructions="i",
    )
    db.add(activity)
    db.commit()

    link_chat_to_activity(db, chat.id, activity.id)

    db.refresh(chat)
    assert chat.activity_id == activity.id


def test_get_chat_session(db: Session):
    chat = create_chat_session(db)
    found = get_chat_session(db, chat.id)
    assert found is not None
    assert found.id == chat.id


def test_get_chat_history(db: Session):
    chat = create_chat_session(db)
    msg = ChatMessage(
        chat_session_id=chat.id,
        role=ChatMessageRole.USER,
        content="Hello",
    )
    db.add(msg)
    db.commit()

    messages = get_chat_history(db, chat.id)
    assert len(messages) == 1
    assert messages[0].content == "Hello"
