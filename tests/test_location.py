"""Maidenhead grid square decoder."""
from backend.utils.location import maidenhead_to_latlon


def test_six_char_grid_resolves_near_known_location():
    # EN66hn is near Marquette, MI (~46.56N, 87.37W). Subsquare center
    # should land within ~5km of the published value (small square: ~3.5 mi
    # wide × ~2.5 mi tall).
    lat, lon = maidenhead_to_latlon("EN66hn")
    assert 46.5 < lat < 46.65
    assert -87.45 < lon < -87.3


def test_four_char_grid_resolves_to_square_center():
    # EN66 spans 1° lat × 2° lon starting at (46N, 88W). Center is
    # (46.5, -87.0).
    lat, lon = maidenhead_to_latlon("EN66")
    assert lat == 46.5
    assert lon == -87.0


def test_grid_is_case_insensitive():
    a = maidenhead_to_latlon("EN66HN")
    b = maidenhead_to_latlon("en66hn")
    assert a == b


def test_invalid_grid_returns_none():
    assert maidenhead_to_latlon("") is None
    assert maidenhead_to_latlon("not-a-grid") is None
    # Single-letter pair only — incomplete.
    assert maidenhead_to_latlon("EN") is None
    # Field letters out of range (S > R is invalid for Maidenhead).
    assert maidenhead_to_latlon("SS66hn") is None


def test_eight_char_extended_grid_accepted():
    """Extended-precision (8-char) locators are accepted but not used for
    extra precision beyond the 6-char subsquare."""
    a = maidenhead_to_latlon("EN66hn55")
    b = maidenhead_to_latlon("EN66hn")
    assert a is not None and b is not None
    # Should be close — both within the same subsquare.
    assert abs(a[0] - b[0]) < 0.05
    assert abs(a[1] - b[1]) < 0.1
