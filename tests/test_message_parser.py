from backend.modules.checkins.message_parser import (
    detect_message_type,
    parse_form_message,
    parse_plain_text_message,
    parse_message,
)
from backend.modules.checkins.models import MessageType


def test_detect_form_message():
    body = "Name: John Smith\nCallsign: W0ABC\nCity: Denver\nCounty: Denver\nState: CO\nMode: Winlink\n"
    assert detect_message_type(body) == MessageType.FORM


def test_detect_plain_text_message():
    body = "John Smith W0ABC Denver Denver CO Winlink All good"
    assert detect_message_type(body) == MessageType.PLAIN_TEXT


def test_detect_unknown_message():
    body = "Hello, this is just a random email with no check-in data."
    assert detect_message_type(body) == MessageType.UNKNOWN


def test_parse_form_message():
    body = (
        "Name: John Smith\n"
        "Callsign: W0ABC\n"
        "City: Denver\n"
        "County: Denver\n"
        "State: CO\n"
        "Mode: Winlink\n"
        "Comments: All good here\n"
    )
    result = parse_form_message(body)
    assert result["name"] == "John Smith"
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["county"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Winlink"
    assert result["comments"] == "All good here"
    assert result["confidence"] == "high"


def test_parse_form_message_with_gps():
    body = (
        "Name: John Smith\n"
        "Callsign: W0ABC\n"
        "City: Denver\n"
        "State: CO\n"
        "Mode: Winlink\n"
        "Latitude: 39.7392\n"
        "Longitude: -104.9903\n"
    )
    result = parse_form_message(body)
    assert result["latitude"] == 39.7392
    assert result["longitude"] == -104.9903


def test_parse_form_message_missing_required():
    body = "Name: John Smith\nCity: Denver\n"
    result = parse_form_message(body)
    assert result["confidence"] == "low"


def test_parse_plain_text_message():
    body = "John Smith, W0ABC, Denver, Denver, CO, Winlink All good here"
    result = parse_plain_text_message(body, known_modes={"Winlink"})
    assert result["name"] == "John Smith"
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["county"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Winlink"
    assert result["comments"] == "All good here"
    assert result["confidence"] == "medium"


def test_parse_plain_text_minimal():
    body = "John, W0ABC, Denver, CO, Winlink"
    result = parse_plain_text_message(body, known_modes={"Winlink"})
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Winlink"
    assert result["confidence"] == "medium"


def test_parse_plain_text_unparseable():
    body = "Hello"
    result = parse_plain_text_message(body)
    assert result["confidence"] == "low"


def test_parse_message_dispatches_form():
    body = "Name: John Smith\nCallsign: W0ABC\nCity: Denver\nState: CO\nMode: Winlink\n"
    msg_type, fields = parse_message(body)
    assert msg_type == MessageType.FORM
    assert fields["callsign"] == "W0ABC"


def test_parse_message_dispatches_plain_text():
    body = "John Smith, W0ABC, Denver, Denver, CO, Winlink"
    msg_type, fields = parse_message(body, known_modes={"Winlink"})
    assert msg_type == MessageType.PLAIN_TEXT
    assert fields["callsign"] == "W0ABC"


def test_parse_message_unknown():
    body = "Random text with no check-in info"
    msg_type, fields = parse_message(body)
    assert msg_type == MessageType.UNKNOWN
    assert fields["confidence"] == "low"


def test_parse_plain_text_custom_modes():
    """Parser uses custom known_modes when provided."""
    body = "John Smith, W0ABC, Denver, CO, VARA-FM Running well"
    result = parse_plain_text_message(body, known_modes={"VARA-FM"})
    assert result["mode"] == "VARA-FM"
    assert result["comments"] == "Running well"


def test_parse_plain_text_comma_form_canonical():
    """The motivating example from the spec parses end-to-end."""
    body = "Ben, KU0HN, Lewiston, Winona, MN, VHF Packet via KU0HN-10"
    result = parse_plain_text_message(body, known_modes={"VHF Packet", "Packet", "Voice"})
    assert result["name"] == "Ben"
    assert result["callsign"] == "KU0HN"
    assert result["city"] == "Lewiston"
    assert result["county"] == "Winona"
    assert result["state"] == "MN"
    assert result["mode"] == "VHF Packet"
    assert result["comments"] == "via KU0HN-10"
    assert result["confidence"] == "medium"


def test_parse_plain_text_comma_form_no_county():
    """5 comma segments map to city + state (no county)."""
    body = "Alice, W0ABC, Denver, CO, Voice good signal"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["name"] == "Alice"
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["county"] is None
    assert result["state"] == "CO"
    assert result["mode"] == "Voice"
    assert result["comments"] == "good signal"


def test_parse_plain_text_callsign_tactical_suffix_stripped():
    body = "Ben, KU0HN-10, Lewiston, MN, Voice"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["callsign"] == "KU0HN"
    # Tightened: prove the primary path ran (not the degraded fallback).
    assert result["name"] == "Ben"
    assert result["city"] == "Lewiston"
    assert result["state"] == "MN"
    assert result["mode"] == "Voice"
    assert result["confidence"] == "medium"


def test_parse_plain_text_multiword_mode_beats_single_word():
    body = "Ben, KU0HN, Lewiston, MN, VARA HF testing"
    result = parse_plain_text_message(body, known_modes={"VARA", "VARA HF"})
    assert result["mode"] == "VARA HF"
    assert result["comments"] == "testing"


def test_parse_plain_text_unknown_mode_marks_low_confidence():
    body = "Ben, KU0HN, Lewiston, MN, SomethingWeird"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["mode"] == ""
    assert result["comments"] == "SomethingWeird"
    assert result["confidence"] == "low"


def test_parse_plain_text_no_commas_degraded_extracts_callsign():
    """Whitespace-only legacy format: extract just the callsign, low confidence."""
    body = "John W0ABC Denver CO Winlink all good"
    result = parse_plain_text_message(body, known_modes={"Winlink"})
    assert result["callsign"] == "W0ABC"
    assert result["name"] == ""
    assert result["city"] is None
    assert result["confidence"] == "low"


def test_parse_plain_text_no_commas_skips_via_gateway():
    """A `via XXXXX-NN` callsign at the end must NOT be picked as the primary."""
    body = "Status update from John W0ABC via KU0HN-10"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["callsign"] == "W0ABC"


def test_parse_plain_text_no_callsign_anywhere_returns_blank():
    body = "Just some text with no callsign"
    result = parse_plain_text_message(body, known_modes={"Voice"})
    assert result["callsign"] == ""
    assert result["confidence"] == "low"


def test_parse_plain_text_mode_match_requires_word_boundary():
    """A mode like 'Packet' must not match inside 'Packetone'."""
    body = "Ben, KU0HN, Lewiston, MN, Packetone test"
    result = parse_plain_text_message(body, known_modes={"Packet"})
    assert result["mode"] == ""
    assert result["comments"] == "Packetone test"


def test_parse_plain_text_canonical_mode_casing_preserved():
    """The stored mode value uses the casing from the known_modes set."""
    body = "Ben, KU0HN, Lewiston, MN, vhf packet"
    result = parse_plain_text_message(body, known_modes={"VHF Packet"})
    assert result["mode"] == "VHF Packet"
