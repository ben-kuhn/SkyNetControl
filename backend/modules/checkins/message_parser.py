import re
import xml.etree.ElementTree as ET

from backend.modules.checkins.models import MessageType

# Callsign pattern: 1-2 letters, digit, 1-3 letters (with optional suffix)
CALLSIGN_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z]{1,3}\b", re.IGNORECASE)

# Strip trailing suffixes from callsigns.
#   `-NN`  AX25 / packet SSID
#   `/X[X]`  portable indicator (`/N` = operating in call-area N away from
#   licensed region, `/M` mobile, `/P` portable, `/MM` maritime mobile, etc.)
_CALLSIGN_SUFFIX_RE = re.compile(r"(?:-\d{1,3}|/[A-Za-z0-9]{1,3})$")

# "via XYZ-NN" pattern that we must NOT pick as the primary callsign
# when degrading the no-comma path.
_VIA_PREFIX_RE = re.compile(r"\bvia\s+$", re.IGNORECASE)

# Form fields we look for (case-insensitive)
FORM_FIELDS = {"name", "callsign", "city", "county", "state", "mode", "comments", "latitude", "longitude", "grid"}
REQUIRED_FORM_FIELDS = {"name", "callsign", "mode"}


def detect_message_type(body: str) -> MessageType:
    """Detect whether the message body is a Winlink form, structured form, or plain text."""
    if "<rms_express_form>" in body.lower():
        return MessageType.WINLINK_FORM
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
        "grid": fields.get("grid"),
        "confidence": confidence,
    }


def _normalize_callsign(token: str) -> str:
    return _CALLSIGN_SUFFIX_RE.sub("", token.upper())


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
        "grid": None,
        "confidence": "low",
    }


# Per-template override map. Filename keys are lowercased.
TEMPLATE_OVERRIDES: dict[str, dict[str, str]] = {
    "winlink_check_in.html": {
        "callsign": "callsign",
        "name": "operator",
        "city": "city",
        "county": "county",
        "state": "state",
        "mode": "modeofcheckin",
        "comments": "comments",
        "latitude": "latitude",
        "longitude": "longitude",
    },
}

# Heuristic patterns, ordered most-specific-first to avoid losing a value
# to a less-specific match (latitude before lat, longitude before lon).
_HEURISTIC_PATTERNS: dict[str, list[str]] = {
    "callsign": ["callsign", "call", "station"],
    "name": ["name", "operator"],
    "city": ["city"],
    "county": ["county", "parish", "borough"],
    "state": ["state", "province"],
    # "session"/"bsession" carries the mode in real PAT Winlink Express
    # XMLs (e.g. <session>VARA HF</session>); fall back to it after the
    # explicit "mode" naming.
    "mode": ["modeofcheckin", "mode", "session"],  # specific first
    "comments": ["comments", "comment", "notes", "message"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "long", "lon"],
    # `grid` matches `<grid>` (PAT) and `grid_square` form fields. Listed
    # specific-first so `grid_square` wins if both were present.
    "grid": ["grid_square", "grid", "locator", "maidenhead"],
}

_LOCATION_VARIABLE_HINTS = ["location", "qth"]
# Variable names that contain a location-hint substring but describe how the
# location was determined rather than a place. PAT/Winlink Express emits
# `<location_source>FORM ENTRY</location_source>`, which used to get swallowed
# as a city by the combined-location fallback.
_LOCATION_VARIABLE_EXCLUDES = ["source", "type"]


def _parse_float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_RMS_FORM_RE = re.compile(
    r"<RMS_Express_Form\b.*?</RMS_Express_Form\s*>",
    re.IGNORECASE | re.DOTALL,
)


def extract_form_xml(body: str) -> str | None:
    """Return just the `<RMS_Express_Form>...</RMS_Express_Form>` chunk.

    PAT writes B2F bodies as a human-readable form rendering, then the XML,
    then a FormData key/value block. The whole body is not valid XML, so we
    slice out just the form element before parsing.
    """
    if not body:
        return None
    match = _RMS_FORM_RE.search(body)
    return match.group(0) if match else None


def extract_form_variables(root: ET.Element) -> dict[str, str]:
    """Pull form variables from a `<RMS_Express_Form>` root, lowercased.

    Two shapes occur in the wild:
      - `<var name="MsgSender">W9GM</var>` (older / hand-authored fixtures)
      - `<msgsender>W9GM</msgsender>` (what PAT and Winlink Express emit)
    Both are accepted; empty-string values are preserved.
    """
    variables: dict[str, str] = {}
    container = root.find(".//variables")
    if container is None:
        return variables
    for child in container:
        if child.tag.lower() == "var":
            name = (child.get("name") or "").strip().lower()
        else:
            name = child.tag.strip().lower()
        if not name:
            continue
        variables[name] = (child.text or "").strip()
    return variables


def parse_winlink_form_message(body: str, known_modes: set[str] | None = None) -> dict:
    """Parse a Winlink Express form (`<RMS_Express_Form>` XML) check-in body.

    Falls back to `parse_plain_text_message` on malformed XML so we never
    silently drop a message.
    """
    if known_modes is None:
        known_modes = set()

    xml_chunk = extract_form_xml(body) or body
    try:
        root = ET.fromstring(xml_chunk)
    except ET.ParseError:
        # Malformed XML — degrade to the plain-text path so the body is still
        # processed (low confidence, but not silently lost).
        return parse_plain_text_message(body, known_modes=known_modes)

    template_filename = ""
    df = root.find(".//form_parameters/display_form")
    if df is not None and df.text:
        template_filename = df.text.strip()

    variables = extract_form_variables(root)

    fields: dict[str, str | float | None] = {
        "name": "",
        "callsign": "",
        "city": None,
        "county": None,
        "state": None,
        "mode": "",
        "comments": None,
        "latitude": None,
        "longitude": None,
        "grid": None,
    }

    # Override pass.
    override = TEMPLATE_OVERRIDES.get(template_filename.lower())
    if override:
        for field, var_name in override.items():
            raw_value = variables.get(var_name.lower(), "")
            if not raw_value:
                continue
            if field in ("latitude", "longitude"):
                fields[field] = _parse_float_or_none(raw_value)
            elif field == "callsign":
                fields[field] = raw_value.upper()
            elif field in ("city", "county", "state", "comments"):
                fields[field] = raw_value
            else:
                fields[field] = raw_value

    # Heuristic pass — fill anything still unset.
    for field, patterns in _HEURISTIC_PATTERNS.items():
        # "Unset" means empty string for callsign/name/mode, None for the rest.
        if field in ("callsign", "name", "mode"):
            if fields[field]:
                continue
        else:
            if fields[field] is not None:
                continue

        for pattern in patterns:
            for var_name, var_value in variables.items():
                if pattern in var_name and var_value:
                    if field in ("latitude", "longitude"):
                        fields[field] = _parse_float_or_none(var_value)
                    elif field == "callsign":
                        fields[field] = var_value.upper()
                    elif field in ("city", "county", "state", "comments"):
                        fields[field] = var_value
                    else:
                        fields[field] = var_value
                    break
            if (field in ("callsign", "name", "mode") and fields[field]) or \
               (field not in ("callsign", "name", "mode") and fields[field] is not None):
                break

    # Comments re-parse: if comments are present and any core field is still
    # missing, re-run Spec A's plain-text parser over the comments string and
    # merge in anything it produced. Runs BEFORE the combined-location
    # fallback so a structured `<comments>` line wins over a free-form
    # `<location>` field (the Winlink Check-in V5 form puts descriptive text
    # like "Home away from home" or "EOC" in `<location>`, never city/state).
    used_comments_reparse = False
    if fields["comments"]:
        missing_core = (
            not fields["name"]
            or not fields["callsign"]
            or not fields["mode"]
            or fields["city"] is None
            or fields["county"] is None
            or fields["state"] is None
        )
        if missing_core:
            reparse = parse_plain_text_message(fields["comments"], known_modes=known_modes)
            for field in ("name", "callsign", "city", "county", "state", "mode"):
                # Only fill if currently empty/None.
                empty = (
                    (field in ("name", "callsign", "mode") and not fields[field])
                    or (field in ("city", "county", "state") and fields[field] is None)
                )
                if empty:
                    reparse_value = reparse.get(field)
                    if reparse_value:
                        fields[field] = reparse_value
                        used_comments_reparse = True

    # Combined-location fallback (only if city/county/state all still unset
    # after comments re-parse — last resort for older / simpler forms).
    if fields["city"] is None and fields["county"] is None and fields["state"] is None:
        for var_name, var_value in variables.items():
            if not var_value:
                continue
            if any(skip in var_name for skip in _LOCATION_VARIABLE_EXCLUDES):
                continue
            if any(hint in var_name for hint in _LOCATION_VARIABLE_HINTS):
                parts = [p.strip() for p in var_value.split(",") if p.strip()]
                if len(parts) >= 3:
                    fields["city"], fields["county"], fields["state"] = parts[0], parts[1], parts[2]
                elif len(parts) == 2:
                    fields["city"], fields["state"] = parts[0], parts[1]
                elif len(parts) == 1:
                    fields["city"] = parts[0]
                break

    # Confidence.
    have_core = bool(fields["callsign"] and fields["name"] and fields["mode"])
    if have_core and not used_comments_reparse:
        confidence = "high"
    elif have_core and used_comments_reparse:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "name": fields["name"] or "",
        "callsign": fields["callsign"] or "",
        "city": fields["city"],
        "county": fields["county"],
        "state": fields["state"],
        "mode": fields["mode"] or "",
        "comments": fields["comments"],
        "latitude": fields["latitude"],
        "longitude": fields["longitude"],
        "grid": fields["grid"],
        "confidence": confidence,
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

    # Canonical ordering is `Name, Callsign, ...` but operators sometimes
    # write `Callsign, Name, ...`. Accept whichever of the first two
    # segments looks like a callsign, preferring position [1].
    seg1_callsign = _normalize_callsign(segments[1])
    seg0_callsign = _normalize_callsign(segments[0])
    if CALLSIGN_RE.fullmatch(seg1_callsign) is not None:
        name = segments[0]
        callsign = seg1_callsign
    elif CALLSIGN_RE.fullmatch(seg0_callsign) is not None:
        name = segments[1]
        callsign = seg0_callsign
    else:
        return _degraded_extract(text)

    # If the last segment is itself a (suffixed) callsign, it's almost
    # certainly a relay/gateway note rather than a mode — peel it into a
    # `via XXXX-NN` comment so the real mode (now segment[-2]) gets matched.
    trailing_callsign = _normalize_callsign(segments[-1])
    extra_comment: str | None = None
    if (
        len(segments) >= 5
        and CALLSIGN_RE.fullmatch(trailing_callsign) is not None
        and trailing_callsign != callsign
    ):
        extra_comment = f"via {segments[-1]}"
        trailing = segments[-2]
        location_segments = segments[2:-2]
    else:
        location_segments = segments[2:-1]
        trailing = segments[-1]

    city, county, state = _assign_location(location_segments)
    mode, comments = _match_known_mode(trailing, known_modes)

    # `_match_known_mode` is strict — the mode token has to be a prefix of
    # the trailing segment. Many real check-ins put the band ("HF") in
    # front of the protocol and a relay/gateway note after it, which the
    # prefix match can't see. Fall back to the canonicalizer: if it spots
    # a recognized protocol token anywhere in the trailing segment, take
    # that. The original trailing is discarded rather than kept as a
    # comment, since band/gateway noise isn't useful data to surface.
    if not mode:
        from backend.modules.checkins.mode_normalize import CANONICAL_MODES, normalize_mode
        normalized = normalize_mode(trailing)
        if normalized in CANONICAL_MODES:
            mode = normalized
            comments = None

    if extra_comment:
        comments = f"{comments} {extra_comment}".strip() if comments else extra_comment

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
        "grid": None,
        "confidence": confidence,
    }


def parse_message(body: str, known_modes: set[str] | None = None) -> tuple[MessageType, dict]:
    """Detect message type and parse accordingly."""
    msg_type = detect_message_type(body)
    if msg_type == MessageType.WINLINK_FORM:
        return msg_type, parse_winlink_form_message(body, known_modes=known_modes)
    if msg_type == MessageType.FORM:
        return msg_type, parse_form_message(body)
    if msg_type == MessageType.PLAIN_TEXT:
        return msg_type, parse_plain_text_message(body, known_modes=known_modes)
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
        "grid": None,
        "confidence": "low",
    }
