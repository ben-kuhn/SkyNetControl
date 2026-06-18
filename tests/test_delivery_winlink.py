import tempfile
from pathlib import Path

from backend.integrations.delivery.backends.winlink import WinlinkBackend
from backend.integrations.delivery.backends.base import DeliveryResult


def test_winlink_backend_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        config = {
            "target_address": "W0NE@winlink.org",
            "mailbox_path": tmpdir,
            "callsign": "W0NE",
        }

        backend = WinlinkBackend()
        result = backend.send("Test Subject", "Test Body", config)

        assert result.success is True
        assert result.error is None

        b2f_files = list(out_dir.glob("*.b2f"))
        assert len(b2f_files) == 1

        content = b2f_files[0].read_text()
        assert "Mid:" in content
        assert "From: W0NE" in content
        assert "To: W0NE@winlink.org" in content
        assert "Subject: Test Subject" in content
        assert "Test Body" in content


def test_winlink_backend_no_out_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = {
            "target_address": "W0NE@winlink.org",
            "mailbox_path": tmpdir,
            "callsign": "W0NE",
        }

        backend = WinlinkBackend()
        result = backend.send("Test Subject", "Test Body", config)

        assert result.success is True
        out_dir = Path(tmpdir) / "out"
        assert out_dir.is_dir()


def test_winlink_backend_no_mailbox_path():
    config = {
        "target_address": "W0NE@winlink.org",
        "mailbox_path": "",
        "callsign": "W0NE",
    }

    backend = WinlinkBackend()
    result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "not configured" in result.error.lower()


def test_winlink_b2f_format():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        config = {
            "target_address": "NET@winlink.org",
            "mailbox_path": tmpdir,
            "callsign": "W0NE",
        }

        backend = WinlinkBackend()
        backend.send("Weekly Roster", "Line 1\nLine 2", config)

        b2f_file = list(out_dir.glob("*.b2f"))[0]
        content = b2f_file.read_text()
        lines = content.split("\n")

        header_keys = [line.split(":")[0] for line in lines if ":" in line and lines.index(line) < 10]
        assert "Mid" in header_keys
        assert "From" in header_keys
        assert "To" in header_keys
        assert "Subject" in header_keys
        assert "Body" in header_keys

        assert "Line 1\nLine 2" in content


def test_winlink_strips_crlf_in_subject_blocks_b2f_header_injection():
    """B2F header-injection guard.

    Subject is rendered from Jinja2 templates that may interpolate user
    text (e.g. a check-in comment). The .b2f format is line-oriented, so
    a Subject containing \\r\\n would inject additional headers. The
    backend must strip CR/LF from header-bound values.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        config = {
            "target_address": "NET@winlink.org",
            "mailbox_path": tmpdir,
            "callsign": "W0NE",
        }
        WinlinkBackend().send(
            "Roster\nCc: attacker@evil.invalid\nX-Injected: yes",
            "body",
            config,
        )
        b2f = list((Path(tmpdir) / "out").glob("*.b2f"))[0].read_text()
        # The header block (everything before the blank line) must contain
        # no extra header *lines* introduced by the injection — the injected
        # tokens fold into the Subject line as inert text instead.
        header_lines = b2f.split("\n\n", 1)[0].splitlines()
        # Subject line is exactly one line containing all the injected text.
        subject_lines = [line for line in header_lines if line.startswith("Subject:")]
        assert len(subject_lines) == 1
        assert "Cc: attacker" in subject_lines[0]  # folded into Subject, not a new header
        # No header line starts with the injected names.
        for line in header_lines:
            assert not line.startswith("Cc:")
            assert not line.startswith("X-Injected:")
