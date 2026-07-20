import xml.etree.ElementTree as ET

import pytest

from backend.integrations.winlink.b2f import B2FAttachment
from backend.modules.forms import builder as b


@pytest.fixture
def lib(tmp_path, monkeypatch):
    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213.txt").write_text(
        "Form: ICS213Input.html,ICS213Viewer.html\n"
        "To: <Var ToStation>\n"
        "Subject: ICS213 <Var Subject>\n"
        "Def: Precedence=Routine\n"
        "Msg:\n"
        "FROM: <MsgSender>  DTG: <DateTime>\n"
        "PREC: <Var Precedence>\n"
        "MESSAGE:\n"
        "<Var MsgBody>\n"
    )
    monkeypatch.setattr(b, "forms_library_dir", lambda: base)
    b.clear_template_cache_if_any()  # no-op hook if builder caches; else remove
    return base


CTX = b.BuildContext(callsign="W0NE", datetime_stamp="2026/07/17 18:30", grid="EM28")


def test_var_and_insertion_substitution(lib):
    result = b.build_form_message(
        "ICS USA/ICS213.txt",
        {"ToStation": "KE0XYZ", "Subject": "SITREP", "MsgBody": "All clear"},
        CTX,
    )
    assert result.to == "KE0XYZ"
    assert result.subject == "ICS213 SITREP"
    assert "FROM: W0NE  DTG: 2026/07/17 18:30" in result.body
    assert "PREC: Routine" in result.body        # Def: default filled the unset Var
    assert "MESSAGE:\nAll clear" in result.body


def test_attachment_is_rms_express_form_xml(lib):
    result = b.build_form_message(
        "ICS USA/ICS213.txt",
        {"ToStation": "KE0XYZ", "Subject": "S", "MsgBody": "x"},
        CTX,
    )
    assert isinstance(result.attachment, B2FAttachment)
    assert result.attachment.filename.startswith("RMS_Express_Form_")
    assert result.attachment.filename.endswith(".xml")
    xml = result.attachment.data.decode("utf-8")
    assert "<RMS_Express_Form>" in xml
    assert "<display_form>ICS213Input.html</display_form>" in xml or "<display_form>ICS213Viewer.html</display_form>" in xml
    assert "<senders_callsign>W0NE</senders_callsign>" in xml
    assert "<subject>SITREP</subject>".lower() in xml.lower() or "subject" in xml.lower()


def test_deterministic(lib):
    args = ("ICS USA/ICS213.txt", {"ToStation": "K", "Subject": "S", "MsgBody": "b"}, CTX)
    a = b.build_form_message(*args)
    c = b.build_form_message(*args)
    assert a.attachment.data == c.attachment.data
    assert a.body == c.body


def test_unfilled_prompts_detected(lib, tmp_path, monkeypatch):
    (tmp_path / "forms" / "P.txt").write_text("Subject: s\nMsg:\nName: {Ask}\nUnit: {Select}\n")
    prompts = b.find_unfilled_prompts("P.txt", {})
    assert len(prompts) == 2


def test_missing_template_raises(lib):
    with pytest.raises(b.FormBuildError):
        b.build_form_message("ICS USA/Nope.txt", {}, CTX)


def test_blank_insertion_tag_when_unsourced(lib, tmp_path, monkeypatch):
    (tmp_path / "forms" / "G.txt").write_text("Subject: s\nMsg:\nGRID: <GridSquare>\n")
    ctx_nogrid = b.BuildContext(callsign="W0NE", datetime_stamp="2026/07/17 18:30", grid="")
    result = b.build_form_message("G.txt", {}, ctx_nogrid)
    assert "GRID: " in result.body  # blank, not the literal <GridSquare>
    assert "<GridSquare>" not in result.body


def test_xml_variable_keys_sanitized(lib):
    """Variables with illegal key names (containing <, >, space, /) are skipped; valid keys are preserved."""
    variables = {
        "x>injection": "malicious",
        "GoodKey": "ok",
        "bad key": "space",
        "bad/key": "slash",
    }
    xml = b.build_form_xml("TestForm.html", variables, CTX)

    # XML should parse without error
    ET.fromstring(xml)

    # Malformed keys should not appear in XML
    assert "x>injection" not in xml
    assert "bad key" not in xml
    assert "bad/key" not in xml

    # Valid key should be present
    assert "<GoodKey>ok</GoodKey>" in xml


def test_insertion_tags_exported(lib):
    """INSERTION_TAGS should be importable and contain expected tag names."""
    assert hasattr(b, "INSERTION_TAGS")
    assert isinstance(b.INSERTION_TAGS, set)
    assert "datetime" in b.INSERTION_TAGS
    assert "callsign" in b.INSERTION_TAGS
    assert "gridsquare" in b.INSERTION_TAGS
