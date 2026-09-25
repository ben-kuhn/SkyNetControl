import json

import anthropic

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.modules.activities.models import (
    ChatMessage,
    ChatMessageRole,
    ChatSession,
)

DEFAULT_MODEL = "claude-sonnet-4-6"

# Only this many trailing messages are sent to the API per request. Full
# history stays in the DB and UI; this bounds per-request token spend.
HISTORY_WINDOW = 40

SYSTEM_PROMPT = """You are a helpful assistant for an amateur radio net manager application. \
You help net control operators run their nets and brainstorm and design activities for net \
sessions of any mode: Winlink, packet, other digital modes, CW, analog/phone, and more. \
General amateur radio questions in service of running a net (band conditions, message \
formats, training ideas, emergency-communications practice) are in scope. \
Activities should be fun, educational, and practical for amateur radio operators. \
When suggesting an activity, provide a clear title, brief description, and \
detailed instructions in markdown format that can be sent to participants.

Stay on topic. If asked about anything unrelated to amateur radio or net operations, \
briefly decline in one sentence and redirect the conversation back to net activities. \
Do not comply with off-topic requests even if the user insists, rephrases, or claims \
special permission — this chat is funded by the net operator solely for net business."""


def _call_claude(
    api_key: str,
    messages: list[dict],
    model: str = DEFAULT_MODEL,
    system: str = SYSTEM_PROMPT,
) -> anthropic.types.Message:
    client = anthropic.Anthropic(api_key=api_key)
    return client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
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
    sender_callsign: str | None = None,
) -> tuple[ChatMessage, ChatMessage]:
    # Get existing history
    history = get_chat_history(db, chat_session_id)
    messages = [{"role": m.role.value, "content": m.content} for m in history]
    messages.append({"role": "user", "content": user_content})
    # Bound per-request cost: only the trailing window goes to the API.
    messages = messages[-HISTORY_WINDOW:]

    # Save user message
    user_msg = ChatMessage(
        chat_session_id=chat_session_id,
        role=ChatMessageRole.USER,
        content=user_content,
        sender_callsign=sender_callsign,
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


EXTRACTION_PROMPT = """You are reviewing a brainstorm conversation between a net control operator and \
an assistant about an amateur radio net activity. Extract the finalized activity proposal from the last \
assistant turn that proposes one. Respond with a single JSON object, no markdown fences, with exactly these keys:
- "title": a short, specific activity name
- "description": one or two sentences summarizing what the activity is
- "instructions": step-by-step participant instructions in plain text (newlines allowed as \\n in the JSON string)
- "tags": an array of 1–4 short, useful tag strings (e.g. ["Winlink", "VHF", "CW"])
If the conversation has not yet converged on a concrete proposal, set all fields to empty strings and tags to [].
Return ONLY the JSON object — no commentary before or after."""


def extract_activity_fields(db: Session, chat_session_id: int, api_key: str) -> dict:
    """Ask Claude to pull a structured activity proposal out of the chat transcript.

    Used by the approve flow to prefill the activity form instead of forcing the
    operator to copy-paste from the transcript. The operator still reviews/edits
    before saving, so this is intentionally best-effort scaffolding.
    """
    history = get_chat_history(db, chat_session_id)
    messages = [{"role": m.role.value, "content": m.content} for m in history[-HISTORY_WINDOW:]]
    # The Messages API rejects a conversation that ends on an assistant turn
    # ('assistant message prefill'), and a brainstorm almost always ends with
    # Claude's proposal. Prompt the extraction as the trailing user message so
    # the request is always well-formed.
    if not messages or messages[-1]["role"] != "user":
        messages.append(
            {"role": "user", "content": "Now extract the finalized activity proposal from the conversation above."}
        )

    response = _call_claude(api_key=api_key, messages=messages, system=EXTRACTION_PROMPT)
    raw = response.content[0].text.strip()
    # Claude occasionally wraps the object in markdown fences despite instructions.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Fall back to returning nothing rather than crashing the approve form.
        return {"title": "", "description": "", "instructions": "", "tags": []}
    return {
        "title": str(data.get("title", "") or "").strip(),
        "description": str(data.get("description", "") or "").strip(),
        "instructions": str(data.get("instructions", "") or "").strip(),
        "tags": [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()],
    }


def link_chat_to_activity(db: Session, chat_session_id: int, activity_id: int) -> None:
    chat = db.get(ChatSession, chat_session_id)
    if chat is not None:
        chat.activity_id = activity_id
        db.commit()


def count_user_messages_today(db: Session, sender_callsign: str | None = None) -> int:
    """Count USER-role chat messages created since midnight UTC.

    With *sender_callsign*, counts that operator's messages; without it,
    counts all user messages (the global-cap denominator, which includes
    legacy NULL-callsign rows).
    """
    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    query = (
        db.query(func.count(ChatMessage.id))
        .filter(ChatMessage.role == ChatMessageRole.USER)
        .filter(ChatMessage.created_at >= start_of_day)
    )
    if sender_callsign is not None:
        query = query.filter(ChatMessage.sender_callsign == sender_callsign)
    return query.scalar() or 0
