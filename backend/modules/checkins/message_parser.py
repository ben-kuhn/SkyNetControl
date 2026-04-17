import re

from backend.modules.checkins.models import MessageType

# Callsign pattern: 1-2 letters, digit, 1-3 letters (with optional suffix)
CALLSIGN_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z]{1,3}\b", re.IGNORECASE)

# Form fields we look for (case-insensitive)
FORM_FIELDS = {"name", "callsign", "city", "county", "state", "mode", "comments",
               "latitude", "longitude"}
REQUIRED_FORM_FIELDS = {"name", "callsign", "mode"}


def detect_message_type(body: str) -> MessageType:
    """Detect whether the message body is a structured form or plain text."""
    lines = body.strip().splitlines()
    field_count = 0
    for line in lines:
        if ":" in line:
            key = line.split(":", 1)[0].strip().lower()
            if key in FORM_FIELDS:
                field_count += 1

    if field_count >= 3:
        return MessageType.FORM

    if CALLSIGN_RE.search(body):
        return MessageType.PLAIN_TEXT

    return MessageType.UNKNOWN


def parse_form_message(body: str) -> dict:
    """Parse a structured form message into check-in fields."""
    fields: dict = {}
    for line in body.strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key in FORM_FIELDS and value:
            fields[key] = value

    latitude = None
    longitude = None
    if "latitude" in fields:
        try:
            latitude = float(fields.pop("latitude"))
        except ValueError:
            pass
    if "longitude" in fields:
        try:
            longitude = float(fields.pop("longitude"))
        except ValueError:
            pass

    if "callsign" in fields:
        fields["callsign"] = fields["callsign"].upper()

    has_required = all(f in fields for f in REQUIRED_FORM_FIELDS)
    confidence = "high" if has_required else "low"

    return {
        "name": fields.get("name", ""),
        "callsign": fields.get("callsign", ""),
        "city": fields.get("city"),
        "county": fields.get("county"),
        "state": fields.get("state"),
        "mode": fields.get("mode", ""),
        "comments": fields.get("comments"),
        "latitude": latitude,
        "longitude": longitude,
        "confidence": confidence,
    }


def parse_plain_text_message(body: str) -> dict:
    """Parse a plain text check-in message.

    Expected order: name, callsign, city, county, state, mode, comments
    The callsign is used as the anchor point for parsing.
    """
    text = body.strip()

    match = CALLSIGN_RE.search(text)
    if not match:
        return {
            "name": "",
            "callsign": "",
            "city": None,
            "county": None,
            "state": None,
            "mode": "",
            "comments": None,
            "latitude": None,
            "longitude": None,
            "confidence": "low",
        }

    callsign = match.group().upper()
    before_callsign = text[: match.start()].strip()
    after_callsign = text[match.end() :].strip()

    name = before_callsign if before_callsign else ""

    parts = after_callsign.split() if after_callsign else []

    city = None
    county = None
    state = None
    mode = ""
    comments = None

    known_modes = {"winlink", "vara", "ardop", "packet", "pactor", "telnet", "ax.25"}

    mode_idx = None
    for i, part in enumerate(parts):
        if part.lower() in known_modes:
            mode_idx = i
            break

    if mode_idx is not None:
        mode = parts[mode_idx]
        location_parts = parts[:mode_idx]
        comment_parts = parts[mode_idx + 1 :]
        comments = " ".join(comment_parts) if comment_parts else None

        if len(location_parts) >= 3:
            city = location_parts[0]
            county = location_parts[1]
            state = location_parts[2]
        elif len(location_parts) == 2:
            city = location_parts[0]
            state = location_parts[1]
        elif len(location_parts) == 1:
            city = location_parts[0]
    else:
        if len(parts) >= 1:
            city = parts[0]
        if len(parts) >= 2:
            state = parts[1]

    confidence = "medium" if callsign and name else "low"

    return {
        "name": name,
        "callsign": callsign,
        "city": city,
        "county": county,
        "state": state,
        "mode": mode,
        "comments": comments,
        "latitude": None,
        "longitude": None,
        "confidence": confidence,
    }


def parse_message(body: str) -> tuple[MessageType, dict]:
    """Detect message type and parse accordingly."""
    msg_type = detect_message_type(body)

    if msg_type == MessageType.FORM:
        return msg_type, parse_form_message(body)
    elif msg_type == MessageType.PLAIN_TEXT:
        return msg_type, parse_plain_text_message(body)
    else:
        return msg_type, {
            "name": "",
            "callsign": "",
            "city": None,
            "county": None,
            "state": None,
            "mode": "",
            "comments": None,
            "latitude": None,
            "longitude": None,
            "confidence": "low",
        }
