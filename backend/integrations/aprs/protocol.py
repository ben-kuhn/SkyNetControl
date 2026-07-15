"""Pure APRS/APRS-IS protocol helpers: login, filters, and object packets.

No I/O here — everything is a string in, string out, so it's all unit-testable
without a network.
"""
import re
from datetime import datetime, timezone

import aprslib

APRS_DEST = "APZSNC"  # APZ* = experimental destination; SNC = SkyNetControl
OBJECT_SYMBOL_TABLE = "/"
OBJECT_SYMBOL_CODE = "o"  # primary-table 'o' (EOC) — used for all event post objects
BEACON_INTERVAL_S = 600  # normal object re-beacon cadence
APP_VERSION = "0.1.0"


def login_line(callsign: str, filter_spec: str) -> str:
    """APRS-IS login. The passcode is the public 15-bit hash of the base
    callsign (aprslib implements it) — verified login, since we transmit."""
    callsign = callsign.upper()
    line = f"user {callsign} pass {aprslib.passcode(callsign)} vers SkyNetControl {APP_VERSION}"
    if filter_spec:
        line += f" filter {filter_spec}"
    return line


def filter_command(filter_spec: str) -> str:
    """Mid-session filter replacement command."""
    return f"#filter {filter_spec}"


def build_filter(
    callsigns: set[str],
    *,
    range_lat: float | None = None,
    range_lon: float | None = None,
    range_km: float | None = None,
) -> str:
    """Server-side filter: wildcarded buddy terms for each participant's base
    callsign (matches every SSID), plus an optional range term. Empty string
    when there is nothing to ask for — the filtered port sends nothing then."""
    bases = sorted({cs.split("-")[0].upper() for cs in callsigns if cs.strip()})
    terms = [f"b/{base}*" for base in bases]
    if range_lat is not None and range_lon is not None and range_km is not None:
        terms.append(f"r/{range_lat:.4f}/{range_lon:.4f}/{range_km:.0f}")
    return " ".join(terms)


def object_name(post_name: str, taken: set[str]) -> str:
    """APRS object names are max 9 chars. Uppercase, strip non-alphanumerics,
    truncate, uniquify with a numeric suffix on collision."""
    base = re.sub(r"[^A-Z0-9]", "", post_name.upper())[:9] or "POST"
    if base not in taken:
        return base
    for i in range(2, 100):
        suffix = str(i)
        candidate = base[: 9 - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
    raise ValueError(f"Could not uniquify object name for {post_name!r}")


def _fmt_lat(lat: float) -> str:
    hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    degrees = int(lat)
    minutes = (lat - degrees) * 60
    return f"{degrees:02d}{minutes:05.2f}{hemi}"


def _fmt_lon(lon: float) -> str:
    hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    degrees = int(lon)
    minutes = (lon - degrees) * 60
    return f"{degrees:03d}{minutes:05.2f}{hemi}"


def object_packet(
    src_callsign: str,
    name: str,
    lat: float,
    lon: float,
    comment: str,
    *,
    kill: bool = False,
    now: datetime | None = None,
) -> str:
    """APRS object report as a full TNC2 frame. kill=True emits the object
    with the '_' flag so other clients delete it."""
    flag = "_" if kill else "*"
    ts = (now or datetime.now(timezone.utc)).strftime("%d%H%M")
    body = (
        f";{name:<9}{flag}{ts}z"
        f"{_fmt_lat(lat)}{OBJECT_SYMBOL_TABLE}{_fmt_lon(lon)}{OBJECT_SYMBOL_CODE}"
        f"{comment}"
    )
    return f"{src_callsign.upper()}>{APRS_DEST},TCPIP*:{body}"
