import os
import pytest
from datetime import datetime, timezone

from backend.modules.checkins.mailbox_reader import read_mailbox, read_message_file


@pytest.fixture
def mailbox_dir(tmp_path):
    """Create a temp directory with sample PAT-style message files."""
    msg1 = (
        "From: W0ABC@winlink.org\n"
        "Subject: Check-in\n"
        "Date: Thu, 10 Apr 2026 18:30:00 +0000\n"
        "Message-Id: <MSG001@winlink.org>\n"
        "To: w0ne@winlink.org\n"
        "\n"
        "John Smith W0ABC Denver Denver CO Winlink All good here\n"
    )
    msg2 = (
        "From: KD0TST@winlink.org\n"
        "Subject: Net Check-in Form\n"
        "Date: Thu, 10 Apr 2026 18:45:00 +0000\n"
        "Message-Id: <MSG002@winlink.org>\n"
        "To: w0ne@winlink.org\n"
        "\n"
        "Name: Jane Doe\n"
        "Callsign: KD0TST\n"
        "City: Boulder\n"
        "County: Boulder\n"
        "State: CO\n"
        "Mode: Winlink\n"
        "Comments: First time checking in!\n"
    )
    msg3 = (
        "From: N0OTHER@winlink.org\n"
        "Subject: Hello\n"
        "Date: Thu, 10 Apr 2026 19:00:00 +0000\n"
        "Message-Id: <MSG003@winlink.org>\n"
        "To: someone.else@winlink.org\n"
        "\n"
        "This is a different conversation.\n"
    )

    (tmp_path / "MSG001.mime").write_text(msg1)
    (tmp_path / "MSG002.mime").write_text(msg2)
    (tmp_path / "MSG003.mime").write_text(msg3)
    (tmp_path / "not_a_message.txt").write_text("random file")
    return tmp_path


def test_read_single_message(mailbox_dir):
    result = read_message_file(mailbox_dir / "MSG001.mime")
    assert result is not None
    assert result["message_id"] == "<MSG001@winlink.org>"
    assert result["from_address"] == "W0ABC@winlink.org"
    assert result["subject"] == "Check-in"
    assert "John Smith" in result["body"]
    assert result["to_address"] == "w0ne@winlink.org"
    assert isinstance(result["received_at"], datetime)


def test_read_mailbox_filters_by_net_address(mailbox_dir):
    messages = read_mailbox(str(mailbox_dir), net_address="w0ne@winlink.org")
    assert len(messages) == 2
    message_ids = {m["message_id"] for m in messages}
    assert "<MSG001@winlink.org>" in message_ids
    assert "<MSG002@winlink.org>" in message_ids
    assert "<MSG003@winlink.org>" not in message_ids


def test_read_mailbox_empty_dir(tmp_path):
    messages = read_mailbox(str(tmp_path), net_address="w0ne@winlink.org")
    assert messages == []


def test_read_mailbox_nonexistent_dir():
    messages = read_mailbox("/nonexistent/path", net_address="w0ne@winlink.org")
    assert messages == []


def test_read_message_file_malformed(tmp_path):
    bad_file = tmp_path / "bad.mime"
    bad_file.write_text("this is not a valid message")
    result = read_message_file(bad_file)
    assert result is None
