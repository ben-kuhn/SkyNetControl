import pytest

from backend.integrations.winlink.pat_connect import resolve_connect_url


def test_alias_resolves_to_url():
    aliases = {"gw1": "ardop:///KE0GW?freq=7100"}
    url, label = resolve_connect_url({"alias": "gw1"}, aliases)
    assert url == "ardop:///KE0GW?freq=7100"
    assert label == "alias: gw1"


def test_unknown_alias_raises():
    with pytest.raises(ValueError):
        resolve_connect_url({"alias": "nope"}, {})


def test_structured_builds_url_with_freq():
    url, label = resolve_connect_url(
        {"mode": "ardop", "gateway": "KE0GW", "freq": "7100"}, {})
    assert url == "ardop:///KE0GW?freq=7100"
    assert "KE0GW" in label and "7100" in label


def test_structured_without_freq():
    url, label = resolve_connect_url({"mode": "telnet", "gateway": "cms"}, {})
    assert url == "telnet:///cms"


def test_structured_missing_gateway_raises():
    with pytest.raises(ValueError):
        resolve_connect_url({"mode": "ardop"}, {})


def test_bad_mode_raises():
    with pytest.raises(ValueError):
        resolve_connect_url({"mode": "carrierpigeon", "gateway": "X"}, {})
