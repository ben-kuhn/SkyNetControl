import re

from backend.modules.checkins.models import MessageType

# Callsign pattern: 1-2 letters, digit, 1-3 letters (with optional suffix)
CALLSIGN_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z]{1,3}\b", re.IGNORECASE)

# Strip a trailing tactical suffix like "-10" from a callsign.
_TACTICAL_SUFFIX_RE = re.compile(r"-\d{1,3}$")

# "via XYZ-NN" pattern that we must NOT pick as the primary callsign
# when degrading the no-comma path.
_VIA_PREFIX_RE = re.compile(r"\bvia\s+$", re.IGNORECASE)

# Form fields we look for (case-insensitive)
FORM_FIELDS = {"name", "callsign", "city", "county", "state", "mode", "comments", "latitude", "longitude"}
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


def _normalize_callsign(token: str) -> str:
    return _TACTICAL_SUFFIX_RE.sub("", token.upper())


def _assign_location(parts: list[str]) -> tuple[str | None, str | None, str | None]:
    """Map 0-3 trimmed location segments to (city, county, state) by count."""
    if len(parts) >= 3:
        return parts[0] or None, parts[1] or None, parts[2] or None
    if len(parts) == 2:
        return parts[0] or None, None, parts[1] or None
    if len(parts) == 1:
        return parts[0] or None, None, None
    return None, None, None


def _match_known_mode(segment: str, known_modes: set[str]) -> tuple[str, str | None]:
    """Return (mode, comments) for the trailing segment.

    Iterates known modes longest-first; a mode matches when the segment
    starts with it followed by end-of-string or whitespace
    (case-insensitive). The stored mode value preserves the casing from
    the known_modes set. No match: ('', whole_segment_or_None).
    """
    if not segment:
        return "", None
    sorted_modes = sorted(known_modes, key=len, reverse=True)
    lowered = segment.lower()
    for mode in sorted_modes:
        m_lower = mode.lower()
        if not lowered.startswith(m_lower):
            continue
        tail = segment[len(mode):]
        if tail == "" or tail[0].isspace():
            comments = tail.strip() or None
            return mode, comments
    return "", segment.strip() or None


def _degraded_extract(body: str) -> dict:
    """No-comma fallback: extract a primary callsign, skipping `via XXXXX-NN`."""
    callsign = ""
    for m in CALLSIGN_RE.finditer(body):
        # Look at what's immediately before the match (up to 8 chars is plenty).
        prefix = body[max(0, m.start() - 8):m.start()]
        if _VIA_PREFIX_RE.search(prefix):
            continue
        callsign = _normalize_callsign(m.group())
        break
    return {
        "name": "",
        "callsign": callsign,
        "city": None,
        "county": None,
        "state": None,
        "mode": "",
        "comments": None,
        "latitude": None,
        "longitude": None,
        "confidence": "low",
    }


def parse_plain_text_message(body: str, known_modes: set[str] | None = None) -> dict:
    """Parse a plain-text check-in body.

    Primary format (comma-delimited):
        Name, Callsign, City[, County], State, Mode comments

    Anything else falls through to a degraded extract that pulls only the
    primary callsign (skipping `via XXXXX-NN` gateway suffixes).
    """
    if known_modes is None:
        known_modes = set()

    text = body.strip()
    if not text:
        return _degraded_extract(text)

    if "," not in text:
        return _degraded_extract(text)

    segments = [s.strip() for s in text.split(",")]
    # The primary path needs at least Name, Callsign, plus one location/mode segment
    # and a trailing mode segment — 4 segments minimum.
    if len(segments) < 4:
        return _degraded_extract(text)

    normalized_callsign = _normalize_callsign(segments[1])
    if CALLSIGN_RE.fullmatch(normalized_callsign) is None:
        return _degraded_extract(text)

    name = segments[0]
    callsign = normalized_callsign
    location_segments = segments[2:-1]
    trailing = segments[-1]

    city, county, state = _assign_location(location_segments)
    mode, comments = _match_known_mode(trailing, known_modes)

    if callsign and name and mode:
        confidence = "medium"
    else:
        confidence = "low"

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


def parse_message(body: str, known_modes: set[str] | None = None) -> tuple[MessageType, dict]:
    """Detect message type and parse accordingly."""
    msg_type = detect_message_type(body)

    if msg_type == MessageType.FORM:
        return msg_type, parse_form_message(body)
    elif msg_type == MessageType.PLAIN_TEXT:
        return msg_type, parse_plain_text_message(body, known_modes=known_modes)
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
