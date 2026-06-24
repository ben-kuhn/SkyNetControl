"""Normalize free-form Winlink mode strings to canonical names.

Real check-ins (form field, comment trailing token, or hand-typed) use
every possible variation: "VARA HF", "VARA-HF", "HF VARA", "vara hf",
"VHF VARA" (which really means VARA FM since FM happens on VHF/UHF),
"VHF Packet", "Packet VHF", "Packet", etc.

Reducing those to a single canonical token per protocol keeps the
check-in table sortable/groupable and is what the user sees when
reviewing the session.
"""
from __future__ import annotations

import re

# Canonical names operators expect to see in the UI.
VARA_HF = "VARA HF"
VARA_FM = "VARA FM"
PACKET = "Packet"
PACTOR = "PACTOR"
ARDOP = "ARDOP"
MERCURY = "Mercury"
WINMOR = "WINMOR"

# Tokens that imply "FM-side VARA" (i.e. VARA over VHF/UHF FM voice
# radios) when paired with VARA.
_FM_TOKENS = {"fm", "vhf", "uhf"}
_HF_TOKENS = {"hf"}

# Split on any non-alphanumeric run so "VARA-HF" -> ["vara", "hf"] and
# "VHF Packet" -> ["vhf", "packet"]. Lowercase for matching.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t}


def normalize_mode(text: str) -> str:
    """Return the canonical mode name, or the input (stripped) when no
    canonical match applies.

    Examples:
        normalize_mode("VARA HF") -> "VARA HF"
        normalize_mode("vara-hf") -> "VARA HF"
        normalize_mode("HF VARA") -> "VARA HF"
        normalize_mode("VARA") -> "VARA HF"  (HF is the default deployment)
        normalize_mode("VHF VARA") -> "VARA FM"
        normalize_mode("VARA-FM") -> "VARA FM"
        normalize_mode("VHF Packet") -> "Packet"
        normalize_mode("Pactor") -> "PACTOR"
        normalize_mode("ardop") -> "ARDOP"
        normalize_mode("WINMOR") -> "WINMOR"
        normalize_mode("Voice (40m)") -> "Voice (40m)"
    """
    if not text:
        return ""
    tokens = _tokens(text)
    if not tokens:
        return text.strip()

    if "vara" in tokens:
        if tokens & _FM_TOKENS:
            return VARA_FM
        # Unmarked or explicit HF: VARA HF. HF is by far the more
        # common Winlink-net use of VARA, so default there.
        return VARA_HF

    if "packet" in tokens:
        return PACKET

    if "pactor" in tokens:
        return PACTOR

    if "ardop" in tokens:
        return ARDOP

    if "mercury" in tokens:
        return MERCURY

    if "winmor" in tokens:
        return WINMOR

    # No canonical match — preserve the operator's text. The check-in
    # editor lets them clean it up before approval.
    return text.strip()
