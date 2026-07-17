import pytest

from backend.integrations.winlink.b2f import (
    B2FAttachment,
    B2FParseError,
    build_b2f,
    guess_content_type,
    parse_b2f,
    strip_b2f_header_chars,
)


def test_build_body_only_layout():
    out = build_b2f(
        message_id="ABC123", from_addr="W0NE", to_addr="KE0XYZ",
        subject="Hello", mbo="W0NE", date="2026/07/16 18:30", body="all clear",
    )
    text = out.decode("utf-8")
    assert text == (
        "Mid: ABC123\n"
        "From: W0NE\n"
        "To: KE0XYZ\n"
        "Subject: Hello\n"
        "Mbo: W0NE\n"
        "Date: 2026/07/16 18:30\n"
        "Body: 9\n"
        "\n"
        "all clear"
    )


def test_build_with_attachment():
    att = B2FAttachment(filename="RMS_Express_Form_ICS213.xml", content_type="application/xml", data=b"<x/>")
    out = build_b2f(
        message_id="M1", from_addr="W0NE", to_addr="KE0XYZ", subject="Form",
        mbo="W0NE", date="2026/07/16 18:30", body="see form", attachments=[att],
    )
    text = out.decode("utf-8")
    assert "Body: 8\n" in text
    assert "File: 4 RMS_Express_Form_ICS213.xml\n" in text
    assert text.endswith("<x/>")


def test_roundtrip_body_only():
    out = build_b2f(
        message_id="M1", from_addr="W0NE", to_addr="KE0XYZ", subject="Hi",
        mbo="W0NE", date="2026/07/16 18:30", body="body text",
    )
    msg = parse_b2f(out)
    assert msg.headers["Mid"] == "M1"
    assert msg.headers["From"] == "W0NE"
    assert msg.headers["Subject"] == "Hi"
    assert msg.body == "body text"
    assert msg.attachments == []


def test_roundtrip_with_attachments():
    atts = [
        B2FAttachment("form.xml", "application/xml", b"<RMS_Express_Form/>"),
        B2FAttachment("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0binary"),
    ]
    out = build_b2f(
        message_id="M1", from_addr="W0NE", to_addr="KE0XYZ", subject="Multi",
        mbo="W0NE", date="2026/07/16 18:30", body="two files", attachments=atts,
    )
    msg = parse_b2f(out)
    assert msg.body == "two files"
    assert len(msg.attachments) == 2
    assert msg.attachments[0].filename == "form.xml"
    assert msg.attachments[0].data == b"<RMS_Express_Form/>"
    assert msg.attachments[1].filename == "photo.jpg"
    assert msg.attachments[1].data == b"\xff\xd8\xff\xe0binary"


def test_parse_message_id_header_alias():
    raw = b"Message-Id: XYZ\nFrom: W0NE\nSubject: s\nBody: 2\n\nhi"
    msg = parse_b2f(raw)
    assert msg.headers.get("Mid", msg.headers.get("Message-Id")) == "XYZ"
    assert msg.body == "hi"


def test_parse_body_length_honored_not_newline():
    # Body length must be respected exactly — a body containing a newline that
    # would otherwise look like a File: section must not be mis-split.
    body = "line1\nFile: 3 fake\nline2"
    out = build_b2f(
        message_id="M1", from_addr="W0NE", to_addr="KE0XYZ", subject="s",
        mbo="W0NE", date="2026/07/16 18:30", body=body,
    )
    msg = parse_b2f(out)
    assert msg.body == body
    assert msg.attachments == []


class TestMalformed:
    def test_non_numeric_body_len(self):
        with pytest.raises(B2FParseError):
            parse_b2f(b"Mid: M1\nFrom: W0NE\nBody: notanumber\n\nhi")

    def test_file_len_exceeds_remaining(self):
        raw = b"Mid: M1\nFrom: W0NE\nBody: 2\n\nhiFile: 9999 big.xml\nshort"
        with pytest.raises(B2FParseError):
            parse_b2f(raw)

    def test_file_missing_filename(self):
        raw = b"Mid: M1\nFrom: W0NE\nBody: 2\n\nhiFile: 3\nabc"
        with pytest.raises(B2FParseError):
            parse_b2f(raw)

    def test_no_body_header(self):
        with pytest.raises(B2FParseError):
            parse_b2f(b"Mid: M1\nFrom: W0NE\nSubject: s\n\nno body header")


def test_strip_header_chars():
    assert strip_b2f_header_chars("a\r\nCc: evil") == "a Cc: evil"
    assert strip_b2f_header_chars("  trimmed  ") == "trimmed"


def test_guess_content_type():
    assert guess_content_type("form.xml") == "application/xml"
    assert guess_content_type("photo.jpg") == "image/jpeg"
    assert guess_content_type("unknown.zzz") == "application/octet-stream"
