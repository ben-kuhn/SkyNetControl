import pytest

from backend.modules.checkins.mode_normalize import normalize_mode


@pytest.mark.parametrize("text,expected", [
    # VARA HF and its variations.
    ("VARA HF", "VARA HF"),
    ("VARA-HF", "VARA HF"),
    ("HF VARA", "VARA HF"),
    ("vara hf", "VARA HF"),
    ("vara-hf", "VARA HF"),
    ("vara_hf", "VARA HF"),
    ("VARA", "VARA HF"),  # unmarked → HF (more common use)
    # VARA FM and its variations.
    ("VARA FM", "VARA FM"),
    ("VARA-FM", "VARA FM"),
    ("vara fm", "VARA FM"),
    ("VHF VARA", "VARA FM"),
    ("UHF VARA", "VARA FM"),
    ("VARA VHF", "VARA FM"),
    # Packet — any qualifier collapses.
    ("Packet", "Packet"),
    ("VHF Packet", "Packet"),
    ("Packet VHF", "Packet"),
    ("packet", "Packet"),
    ("1200-baud Packet", "Packet"),
    # PACTOR is always all-caps canonically.
    ("Pactor", "PACTOR"),
    ("PACTOR", "PACTOR"),
    ("pactor 3", "PACTOR"),
    # ARDOP, Mercury, WINMOR.
    ("ARDOP", "ARDOP"),
    ("ardop", "ARDOP"),
    ("Mercury", "Mercury"),
    ("mercury", "Mercury"),
    ("WINMOR", "WINMOR"),
    ("winmor", "WINMOR"),
])
def test_known_modes_normalize(text, expected):
    assert normalize_mode(text) == expected


def test_unknown_mode_passes_through_trimmed():
    assert normalize_mode("Voice (40m)") == "Voice (40m)"
    assert normalize_mode("  Winlink  ") == "Winlink"


def test_empty_input():
    assert normalize_mode("") == ""


@pytest.mark.parametrize("text", [
    "VARA VHF",
    "VARA UHF",
    "vhf vara",
    "uhf vara",
    "VARA-FM",
    "VARA FM",
    "FM VARA",
])
def test_vara_paired_with_vhf_uhf_or_fm_means_vara_fm(text):
    """VARA is never used on VHF or UHF — when those bands appear, it's
    always VARA FM (the FM-side flavor)."""
    assert normalize_mode(text) == "VARA FM"


@pytest.mark.parametrize("text", [
    "VARA",
    "VARA HF",
    "VARA-HF",
    "HF VARA",
    "vara",
])
def test_vara_alone_or_with_hf_means_vara_hf(text):
    """Unmarked VARA is HF in practice, since FM is always explicit."""
    assert normalize_mode(text) == "VARA HF"


@pytest.mark.parametrize("text", [
    "Packet",
    "VHF Packet",
    "Packet VHF",
    "UHF Packet",
    "1200-baud Packet",
    "9k6 Packet",
    "9600 baud packet",
    "300 baud packet",
])
def test_any_packet_collapses_to_packet(text):
    """Operators add bands or baud rates to packet check-ins. Strip them
    all — 99% of the time the right canonical label is just "Packet"."""
    assert normalize_mode(text) == "Packet"
