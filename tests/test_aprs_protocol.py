from datetime import datetime, timezone

from backend.integrations.aprs.protocol import (
    _sanitize_comment,
    build_filter,
    filter_command,
    login_line,
    object_name,
    object_packet,
)


class TestLogin:
    def test_login_line_contains_computed_passcode(self):
        line = login_line("N0CALL", "b/N0CALL*")
        assert line.startswith("user N0CALL pass 13023 vers SkyNetControl")
        assert line.endswith("filter b/N0CALL*")

    def test_login_line_without_filter(self):
        line = login_line("n0call", "")
        assert "filter" not in line
        assert "N0CALL" in line


class TestFilter:
    def test_buddy_terms_wildcard_and_strip_ssid(self):
        spec = build_filter({"ke0xyz", "W0NE-9"})
        assert spec == "b/KE0XYZ* b/W0NE*"  # sorted, base callsign, wildcarded

    def test_range_term(self):
        spec = build_filter(set(), range_lat=39.1234, range_lon=-94.5678, range_km=50)
        assert spec == "r/39.1234/-94.5678/50"

    def test_combined(self):
        spec = build_filter({"W0NE"}, range_lat=39.0, range_lon=-94.0, range_km=25)
        assert spec == "b/W0NE* r/39.0000/-94.0000/25"

    def test_empty(self):
        assert build_filter(set()) == ""

    def test_partial_range_ignored(self):
        assert build_filter(set(), range_lat=39.0) == ""

    def test_filter_command(self):
        assert filter_command("b/W0NE*") == "#filter b/W0NE*"


class TestObjectName:
    def test_derivation(self):
        assert object_name("Rest Stop 3", set()) == "RESTSTOP3"

    def test_truncation(self):
        assert object_name("Water Station Alpha", set()) == "WATERSTAT"

    def test_uniquify(self):
        assert object_name("Rest Stop 3", {"RESTSTOP3"}) == "RESTSTOP2"

    def test_empty_falls_back(self):
        assert object_name("!!!", set()) == "POST"


class TestObjectPacket:
    NOW = datetime(2026, 7, 15, 18, 30, 0, tzinfo=timezone.utc)

    def test_live_object(self):
        pkt = object_packet("W0NE", "RESTSTOP3", 39.0625, -94.5786, "SkyNetControl event: Marathon", now=self.NOW)
        assert pkt == (
            "W0NE>APZSNC,TCPIP*:;RESTSTOP3*151830z3903.75N/09434.72Wo"
            "SkyNetControl event: Marathon"
        )

    def test_kill_object(self):
        pkt = object_packet("W0NE", "EOC", 39.0, -94.0, "", kill=True, now=self.NOW)
        # 9-char name space-padded, '_' = kill flag
        assert ";EOC      _151830z" in pkt
        assert pkt.startswith("W0NE>APZSNC,TCPIP*:")

    def test_southern_eastern_hemispheres(self):
        pkt = object_packet("W0NE", "X", -33.8688, 151.2093, "", now=self.NOW)
        assert "3352.13S/15112.56E" in pkt

    def test_comment_crlf_injection_stripped(self):
        # CR/LF are stripped; the surrounding printable chars are joined
        pkt = object_packet("W0NE", "EOC", 39.0, -94.0, "evil\r\nFAKE>APRS:injected", now=self.NOW)
        assert "\r" not in pkt
        assert "\n" not in pkt
        # stripped text is present (truncated at 43 chars) with no injected newline
        assert "evilFAKE>APRS:injected"[:43] in pkt

    def test_comment_truncated_at_43_chars(self):
        # Use chars not present elsewhere in the packet frame
        long_comment = "x" * 42 + "Q" + "Q" * 17  # 60 chars total; position 43 is last Q kept
        pkt = object_packet("W0NE", "MAST", 39.0, -94.0, long_comment, now=self.NOW)
        # Exactly 43 x+Q chars should appear; split on 'o' symbol to isolate comment
        comment_part = pkt.split("Wo", 1)[-1]
        assert len(comment_part) == 43
        assert comment_part == "x" * 42 + "Q"


class TestSanitizeComment:
    def test_strips_cr_lf(self):
        # CR and LF are non-printable and get stripped; surrounding text is joined
        result = _sanitize_comment("hello\r\nworld")
        assert result == "helloworld"
        assert "\r" not in _sanitize_comment("foo\rbar")
        assert "\n" not in _sanitize_comment("foo\nbar")

    def test_truncates_to_43(self):
        assert len(_sanitize_comment("x" * 100)) == 43

    def test_preserves_normal_text(self):
        text = "SkyNetControl event: Marathon"
        assert _sanitize_comment(text) == text


class TestBuildFilterHardened:
    def test_strips_invalid_chars_from_callsign(self):
        spec = build_filter({"KE0 XYZ\r\n", "W0NE"})
        assert "b/KE0XYZ*" in spec
        assert "b/W0NE*" in spec
        assert "\r" not in spec
        assert "\n" not in spec
        assert " " not in spec.replace("b/", "").replace("* ", "*")
