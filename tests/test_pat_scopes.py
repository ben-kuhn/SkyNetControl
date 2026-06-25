import pytest

from backend.auth.scopes import SCOPES, SCOPE_NAMES, ADMIN_SCOPES, PER_NET_SCOPES, validate_pat_scopes


def test_scopes_dict_has_expected_entries():
    assert "schedule:read" in SCOPES
    assert "schedule:write" in SCOPES
    assert "checkins:read" in SCOPES
    assert "checkins:write" in SCOPES
    assert "roster:read" in SCOPES
    assert "map:read" in SCOPES
    assert "users:read" in SCOPES
    assert "users:write" in SCOPES
    assert "config:read" in SCOPES
    assert "config:write" in SCOPES


def test_scope_names_matches_scopes_keys():
    assert SCOPE_NAMES == set(SCOPES.keys())


def test_admin_scopes_are_separate_from_per_net_scopes():
    assert ADMIN_SCOPES.isdisjoint(PER_NET_SCOPES)
    assert ADMIN_SCOPES | PER_NET_SCOPES == SCOPE_NAMES


def test_validate_pat_scopes_admin_can_use_admin_scope():
    validate_pat_scopes(["users:read"], is_admin=True, net_id=None)


def test_validate_pat_scopes_non_admin_cannot_use_admin_scope():
    with pytest.raises(ValueError, match="Only admins"):
        validate_pat_scopes(["users:read"], is_admin=False, net_id=None)


def test_validate_pat_scopes_non_admin_can_use_per_net_scope():
    validate_pat_scopes(["schedule:read"], is_admin=False, net_id=None)


def test_validate_pat_scopes_per_net_scope_with_net_id():
    validate_pat_scopes(["schedule:read"], is_admin=False, net_id=1)


def test_validate_pat_scopes_rejects_unknown_scope():
    with pytest.raises(ValueError, match="invalid:scope"):
        validate_pat_scopes(["invalid:scope"], is_admin=True, net_id=None)


def test_validate_pat_scopes_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        validate_pat_scopes([], is_admin=True, net_id=None)


def test_validate_pat_scopes_admin_can_mix_scopes():
    validate_pat_scopes(["users:read", "schedule:read"], is_admin=True, net_id=None)
