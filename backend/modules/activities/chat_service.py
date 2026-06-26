import anthropic

from sqlalchemy.orm import Session

from backend.modules.activities.models import (
    ChatMessage,
    ChatMessageRole,
    ChatSession,
)

SYSTEM_PROMPT = """You are a helpful assistant for a ham radio Winlink net manager. \
You help brainstorm and design activities for weekly net sessions. \
Activities should be fun, educational, and practical for amateur radio operators. \
When suggesting an activity, provide a clear title, brief description, and \
detailed instructions in markdown format that can be sent to participants."""


def _call_claude(
    api_key: str,
    messages: list[dict],
    model: str = "claude-sonnet-4-20250514",
) -> anthropic.types.Message:
    client = anthropic.Anthropic(api_key=api_key)
    return client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )


def create_chat_session(db: Session) -> ChatSession:
    chat = ChatSession()
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def get_chat_session(db: Session, chat_session_id: int, net_id: int | None = None) -> ChatSession | None:
    """Return a ChatSession by id, optionally verifying it belongs to *net_id* via its linked activity.

    If net_id is provided:
    - A session with no linked activity is considered unscoped and accessible within the net.
    - A session whose linked activity belongs to a different net returns None (cross-net isolation).
    """
    chat = db.get(ChatSession, chat_session_id)
    if chat is None:
        return None
    if net_id is not None and chat.activity_id is not None:
        # Chat is linked to an activity — verify the activity is in the expected net.
        if chat.activity is None or chat.activity.net_id != net_id:
            return None
    return chat


def get_chat_history(db: Session, chat_session_id: int) -> list[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.chat_session_id == chat_session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )


def send_message(
    db: Session,
    chat_session_id: int,
    user_content: str,
    api_key: str,
) -> tuple[ChatMessage, ChatMessage]:
    # Get existing history
    history = get_chat_history(db, chat_session_id)
    messages = [{"role": m.role.value, "content": m.content} for m in history]
    messages.append({"role": "user", "content": user_content})

    # Save user message
    user_msg = ChatMessage(
        chat_session_id=chat_session_id,
        role=ChatMessageRole.USER,
        content=user_content,
    )
    db.add(user_msg)
    db.flush()

    # Call Claude
    response = _call_claude(api_key=api_key, messages=messages)
    assistant_content = response.content[0].text

    # Save assistant message
    assistant_msg = ChatMessage(
        chat_session_id=chat_session_id,
        role=ChatMessageRole.ASSISTANT,
        content=assistant_content,
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(user_msg)
    db.refresh(assistant_msg)

    return user_msg, assistant_msg


def link_chat_to_activity(db: Session, chat_session_id: int, activity_id: int) -> None:
    chat = db.get(ChatSession, chat_session_id)
    if chat is not None:
        chat.activity_id = activity_id
        db.commit()
