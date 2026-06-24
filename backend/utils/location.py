"""Location utilities — Maidenhead grid square decoding."""
from __future__ import annotations

import re

# Strict Maidenhead locator: 2 letters (field), 2 digits (square), and
# optionally 2 letters (subsquare). 8-char locators (extended subsquare,
# 2 more digits) are accepted but the extended digits add little
# precision over the 6-char center for our purposes.
_GRID_RE = re.compile(r"^([A-R])([A-R])([0-9])([0-9])(?:([A-X])([A-X]))?(?:[0-9]{2})?$", re.IGNORECASE)


def maidenhead_to_latlon(grid: str) -> tuple[float, float] | None:
    """Return the (latitude, longitude) center of a Maidenhead grid locator.

    Accepts 4-char ("EN66"), 6-char ("EN66hn"), or 8-char extended
    locators. Returns None for malformed input. The returned point is the
    center of the smallest square encoded — useful as a "we know roughly
    where this op was" pin when no precise coordinates were provided.
    """
    if not grid:
        return None
    cleaned = grid.strip().replace(" ", "")
    m = _GRID_RE.match(cleaned)
    if m is None:
        return None

    field_lon, field_lat, sq_lon, sq_lat, sub_lon, sub_lat = m.groups()

    # Field: 20° lon × 10° lat. A..R = 0..17.
    lon = (ord(field_lon.upper()) - ord("A")) * 20 - 180
    lat = (ord(field_lat.upper()) - ord("A")) * 10 - 90

    # Square: 2° lon × 1° lat.
    lon += int(sq_lon) * 2
    lat += int(sq_lat) * 1

    if sub_lon is not None and sub_lat is not None:
        # Subsquare: 5' lon × 2.5' lat = 5/60° × 2.5/60° (a..x = 0..23).
        lon += (ord(sub_lon.lower()) - ord("a")) * (5 / 60)
        lat += (ord(sub_lat.lower()) - ord("a")) * (2.5 / 60)
        # Center of the subsquare.
        lon += (5 / 60) / 2
        lat += (2.5 / 60) / 2
    else:
        # Center of the square.
        lon += 2 / 2
        lat += 1 / 2

    return (round(lat, 6), round(lon, 6))
