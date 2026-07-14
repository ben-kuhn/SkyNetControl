from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.activities import chat_service
from backend.modules.activities.chat_service import (
    HISTORY_WINDOW,
    SYSTEM_PROMPT,
    create_chat_session,
    get_chat_history,
    get_chat_session,
    link_chat_to_activity,
    send_message,
)
from backend.modules.activities.models import (
    Activity,
    ChatMessage,
    ChatMessageRole,
)
import backend.modules.schedule.models  # noqa: F401


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
def fake_claude(monkeypatch):
    """Replace _call_claude; capture the messages payload it receives."""
    calls = []

    def _fake(api_key, messages, model=chat_service.DEFAULT_MODEL):
        calls.append({"api_key": api_key, "messages": messages, "model": model})
        return SimpleNamespace(content=[SimpleNamespace(text="A fun activity idea")])

    monkeypatch.setattr(chat_service, "_call_claude", _fake)
    return calls


def test_default_model_is_current_sonnet():
    assert chat_service.DEFAULT_MODEL == "claude-sonnet-4-6"


def test_system_prompt_scopes_to_amateur_radio_and_declines_off_topic():
    lowered = SYSTEM_PROMPT.lower()
    assert "amateur radio" in lowered
    assert "decline" in lowered
    # Multi-mode scope: not just Winlink.
    for mode in ("winlink", "cw", "packet"):
        assert mode in lowered


def test_send_message_truncates_history_to_window(db: Session, fake_claude):
    chat = create_chat_session(db)
    # Seed more history than the window (alternating user/assistant).
    for i in range(HISTORY_WINDOW + 10):
        role = "user" if i % 2 == 0 else "assistant"
        db.add(
            chat_service.ChatMessage(
                chat_session_id=chat.id,
                role=chat_service.ChatMessageRole(role),
                content=f"message {i}",
            )
        )
    db.commit()

    send_message(db, chat.id, "one more idea please", api_key="k")

    sent = fake_claude[0]["messages"]
    assert len(sent) == HISTORY_WINDOW
    assert sent[-1] == {"role": "user", "content": "one more idea please"}


def test_send_message_short_history_not_padded(db: Session, fake_claude):
    chat = create_chat_session(db)
    send_message(db, chat.id, "first message", api_key="k")
    sent = fake_claude[0]["messages"]
    assert sent == [{"role": "user", "content": "first message"}]


def test_send_message_records_sender_callsign(db: Session, fake_claude):
    chat = create_chat_session(db)
    user_msg, assistant_msg = send_message(
        db, chat.id, "brainstorm a packet night", api_key="k", sender_callsign="W0NC"
    )
    assert user_msg.sender_callsign == "W0NC"
    assert assistant_msg.sender_callsign is None


# --- Restored tests (lost in Task 1 overwrite) ---


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
        user_msg, assistant_msg = send_message(db, chat.id, "I want an emergency prep activity", api_key="test-key")

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
    from tests.conftest import make_test_net

    make_test_net(db)
    from backend.modules.nets.models import Net

    net_id = db.query(Net).filter(Net.slug == "t").one().id
    chat = create_chat_session(db)
    activity = Activity(
        net_id=net_id,
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


def test_count_user_messages_today(db: Session, fake_claude):
    chat = create_chat_session(db)
    send_message(db, chat.id, "idea one", api_key="k", sender_callsign="W0NC")
    send_message(db, chat.id, "idea two", api_key="k", sender_callsign="W0NC")
    send_message(db, chat.id, "idea three", api_key="k", sender_callsign="W0NE")
    # Legacy row without attribution counts globally but not per-user.
    db.add(
        chat_service.ChatMessage(
            chat_session_id=chat.id,
            role=chat_service.ChatMessageRole.USER,
            content="legacy",
        )
    )
    db.commit()

    assert chat_service.count_user_messages_today(db, "W0NC") == 2
    assert chat_service.count_user_messages_today(db, "W0NE") == 1
    # Global count: 4 user messages; assistant replies are not counted.
    assert chat_service.count_user_messages_today(db) == 4
