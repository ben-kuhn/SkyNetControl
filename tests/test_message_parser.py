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


CHECKIN_FORM_BODY = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters>
    <xml_file_version>1.0</xml_file_version>
    <display_form>Winlink_Check_in.html</display_form>
  </form_parameters>
  <variables>
    <var name="Callsign">KU0HN</var>
    <var name="Operator">Ben Kuhn</var>
    <var name="City">Lewiston</var>
    <var name="County">Winona</var>
    <var name="State">MN</var>
    <var name="ModeOfCheckin">VHF Packet</var>
    <var name="Comments">via KU0HN-10</var>
    <var name="Latitude">44.0</var>
    <var name="Longitude">-91.8</var>
  </variables>
</RMS_Express_Form>
"""


def test_detect_winlink_form_body():
    """A body with <RMS_Express_Form> is detected before any other branch runs."""
    assert detect_message_type(CHECKIN_FORM_BODY) == MessageType.WINLINK_FORM


def test_parse_winlink_form_canonical():
    from backend.modules.checkins.message_parser import parse_winlink_form_message
    result = parse_winlink_form_message(CHECKIN_FORM_BODY, known_modes={"VHF Packet"})
    assert result["callsign"] == "KU0HN"
    assert result["name"] == "Ben Kuhn"
    assert result["city"] == "Lewiston"
    assert result["county"] == "Winona"
    assert result["state"] == "MN"
    assert result["mode"] == "VHF Packet"
    assert result["comments"] == "via KU0HN-10"
    assert result["latitude"] == 44.0
    assert result["longitude"] == -91.8
    assert result["confidence"] == "high"


def test_parse_winlink_form_heuristic_only():
    """Variable names that don't match the override map still resolve via heuristic substring."""
    from backend.modules.checkins.message_parser import parse_winlink_form_message
    body = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters><display_form>Something_Else.html</display_form></form_parameters>
  <variables>
    <var name="Senders_Callsign">W0ABC</var>
    <var name="Operator_Name">John</var>
    <var name="QTH_City">Denver</var>
    <var name="QTH_State">CO</var>
    <var name="Reporting_Mode">Voice</var>
  </variables>
</RMS_Express_Form>
"""
    result = parse_winlink_form_message(body, known_modes={"Voice"})
    assert result["callsign"] == "W0ABC"
    assert result["name"] == "John"
    assert result["city"] == "Denver"
    assert result["state"] == "CO"
    assert result["mode"] == "Voice"
    assert result["confidence"] == "high"


def test_parse_winlink_form_combined_location_splits():
    """A single 'location' variable comma-splits into city/county/state."""
    from backend.modules.checkins.message_parser import parse_winlink_form_message
    body = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters><display_form>x.html</display_form></form_parameters>
  <variables>
    <var name="Call">KU0HN</var>
    <var name="Name">Ben</var>
    <var name="Location">Lewiston, Winona, MN</var>
    <var name="Mode">VHF Packet</var>
  </variables>
</RMS_Express_Form>
"""
    result = parse_winlink_form_message(body, known_modes={"VHF Packet"})
    assert result["city"] == "Lewiston"
    assert result["county"] == "Winona"
    assert result["state"] == "MN"
    assert result["mode"] == "VHF Packet"


def test_parse_winlink_form_comments_reparse_fills_mode():
    """If mode is missing from variables but appears in comments, the re-parse picks it up."""
    from backend.modules.checkins.message_parser import parse_winlink_form_message
    body = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters><display_form>x.html</display_form></form_parameters>
  <variables>
    <var name="Callsign">KU0HN</var>
    <var name="Name">Ben</var>
    <var name="Comments">Ben, KU0HN, Lewiston, MN, Voice all good</var>
  </variables>
</RMS_Express_Form>
"""
    result = parse_winlink_form_message(body, known_modes={"Voice"})
    assert result["mode"] == "Voice"
    assert result["city"] == "Lewiston"
    assert result["state"] == "MN"
    # Confidence is medium because mode came from comments re-parse, not structured form.
    assert result["confidence"] == "medium"


def test_parse_winlink_form_malformed_xml_falls_through():
    """A body that looks like a winlink form but is broken XML falls through to plain-text."""
    body = "<RMS_Express_Form><variables><var name=callsign>oops, no quotes"
    msg_type, fields = parse_message(body, known_modes={"Voice"})
    # detect_message_type still returns WINLINK_FORM (substring matched),
    # but the parser falls through and dispatches to plain-text on the body.
    # The plain-text parser will degrade further (no commas in the way it expects),
    # so we mostly assert "doesn't raise" + low confidence.
    assert fields["confidence"] == "low"


def test_parse_winlink_form_non_form_body_unchanged():
    """A body with no <RMS_Express_Form> wrapper still goes through the Spec A paths."""
    body = "Ben, KU0HN, Lewiston, MN, Voice"
    msg_type, fields = parse_message(body, known_modes={"Voice"})
    assert msg_type == MessageType.PLAIN_TEXT
    assert fields["callsign"] == "KU0HN"


def test_parse_winlink_form_dispatched_by_parse_message():
    msg_type, fields = parse_message(CHECKIN_FORM_BODY, known_modes={"VHF Packet"})
    assert msg_type == MessageType.WINLINK_FORM
    assert fields["callsign"] == "KU0HN"


# PAT-delivered B2F bodies are NOT pure XML — the human-readable form
# rendering precedes the XML and a FormData key/value block follows it.
# Variables also use element-tag-as-name (`<msgsender>W9GM</msgsender>`)
# rather than `<var name="msgsender">W9GM</var>`. The parser must handle
# both shapes.
PAT_B2F_CHECKIN_BODY = """Winlink Check-in
0. HEADER
  0a: Organization:\tW0NE Winlink Net
  0b: Subject:\tWinlink Check-in EXERCISE - W9GM - Home
  1c. From:\tW9GM

-------------------------------------------------------------
4a COMMENTS:

Ken, W9GM/8, Marquette, Marquette, MI, USA, VARA HF, 40M

-------------------------------------------------------------

<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters>
    <display_form>Winlink_Check_In_Viewer.html</display_form>
    <senders_callsign>W9GM</senders_callsign>
  </form_parameters>
  <variables>
    <msgto>W0NE</msgto>
    <msgsender>W9GM</msgsender>
    <name>Ken Weigel</name>
    <comments>Ken, W9GM/8, Marquette, Marquette, MI, USA, VARA HF, 40M</comments>
    <session>VARA HF</session>
    <testcall>W9GM</testcall>
  </variables>
</RMS_Express_Form>

Sender:T=W9GM
1c. From:T=W9GM
"""


def test_parse_winlink_form_pat_b2f_body_uses_xml_not_prose():
    """A PAT B2F message body wraps XML in human-readable prose + form-data.
    Parser must extract the XML region and read element-tag-as-name vars,
    not fall through to plain-text and pick up the Organization callsign.
    """
    from backend.modules.checkins.message_parser import parse_winlink_form_message
    result = parse_winlink_form_message(PAT_B2F_CHECKIN_BODY, known_modes={"VARA HF"})
    # Without the fix this returns 'W0NE' from the Organization line.
    assert result["callsign"] == "W9GM"
    assert result["name"] == "Ken Weigel"
