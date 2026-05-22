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
    body = "John Smith W0ABC Denver Denver CO Winlink All good here"
    result = parse_plain_text_message(body)
    assert result["name"] == "John Smith"
    assert result["callsign"] == "W0ABC"
    assert result["city"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Winlink"
    assert result["confidence"] == "medium"


def test_parse_plain_text_minimal():
    body = "John W0ABC Denver CO Winlink"
    result = parse_plain_text_message(body)
    assert result["callsign"] == "W0ABC"
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
    body = "John Smith W0ABC Denver Denver CO Winlink"
    msg_type, fields = parse_message(body)
    assert msg_type == MessageType.PLAIN_TEXT
    assert fields["callsign"] == "W0ABC"


def test_parse_message_unknown():
    body = "Random text with no check-in info"
    msg_type, fields = parse_message(body)
    assert msg_type == MessageType.UNKNOWN
    assert fields["confidence"] == "low"


def test_parse_plain_text_custom_modes():
    """Parser uses custom known_modes when provided."""
    body = "John Smith W0ABC Denver CO VARA-FM Running well"
    result = parse_plain_text_message(body, known_modes={"vara-fm"})
    assert result["mode"] == "VARA-FM"
    assert result["comments"] == "Running well"


def test_parse_plain_text_default_modes_still_work():
    """Without known_modes param, defaults still work."""
    body = "John Smith W0ABC Denver CO Winlink"
    result = parse_plain_text_message(body)
    assert result["mode"] == "Winlink"
