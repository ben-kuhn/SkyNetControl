from types import SimpleNamespace

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
    send_message,
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
