"""Normalize free-form Winlink mode strings to canonical names.

Real check-ins (form field, comment trailing token, or hand-typed) use
every possible variation: "VARA", "VARA HF", "VARA-HF", "HF VARA",
"vara hf", "VHF VARA" (which really means VARA FM since FM happens on
VHF/UHF), "VHF Packet", "Packet VHF", "1200-baud Packet", etc.

Reducing those to a single canonical token per protocol keeps the
check-in table sortable/groupable and is what the user sees when
reviewing the session.
"""
from __future__ import annotations

import re

# Canonical names operators expect to see in the UI.
# VARA on HF is just "VARA" — there's no separate "VARA HF" product
# name; the HF qualifier is redundant. "VARA FM" is a distinct product
# for FM voice radios on VHF/UHF.
VARA = "VARA"
VARA_FM = "VARA FM"
PACKET = "Packet"
PACTOR = "PACTOR"
ARDOP = "ARDOP"
MERCURY = "Mercury"
WINMOR = "WINMOR"

# Tokens that imply "FM-side VARA" when paired with VARA. VARA itself
# is never used on VHF/UHF — those bands always mean VARA FM.
_FM_TOKENS = {"fm", "vhf", "uhf"}

# Split on any non-alphanumeric run so "VARA-HF" -> ["vara", "hf"] and
# "VHF Packet" -> ["vhf", "packet"]. Lowercase for matching.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t}


def normalize_mode(text: str) -> str:
    """Return the canonical mode name, or the input (stripped) when no
    canonical match applies.

    Examples:
        normalize_mode("VARA") -> "VARA"
        normalize_mode("VARA HF") -> "VARA"
        normalize_mode("vara-hf") -> "VARA"
        normalize_mode("HF VARA") -> "VARA"
        normalize_mode("HF VARA gateway W1ABC") -> "VARA"
        normalize_mode("VHF VARA") -> "VARA FM"
        normalize_mode("VARA-FM") -> "VARA FM"
        normalize_mode("VHF Packet") -> "Packet"
        normalize_mode("1200-baud Packet") -> "Packet"
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
        # VARA itself only runs on HF; "VARA FM" is the separate
        # FM-side product. So any FM/VHF/UHF token means VARA FM.
        if tokens & _FM_TOKENS:
            return VARA_FM
        return VARA

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
