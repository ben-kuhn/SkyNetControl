import pytest

from backend.auth.scopes import SCOPES, SCOPE_NAMES, validate_scopes_for_role
from backend.auth.models import UserRole


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
    assert len(SCOPES) == 10


def test_scope_names_matches_scopes_keys():
    assert SCOPE_NAMES == set(SCOPES.keys())


def test_validate_scopes_viewer_can_read_schedule():
    validate_scopes_for_role(["schedule:read"], UserRole.VIEWER)


def test_validate_scopes_viewer_cannot_write_schedule():
    with pytest.raises(ValueError, match="schedule:write"):
        validate_scopes_for_role(["schedule:write"], UserRole.VIEWER)


def test_validate_scopes_net_control_can_write_schedule():
    validate_scopes_for_role(["schedule:write"], UserRole.NET_CONTROL)


def test_validate_scopes_admin_can_use_all():
    validate_scopes_for_role(list(SCOPES.keys()), UserRole.ADMIN)


def test_validate_scopes_rejects_unknown_scope():
    with pytest.raises(ValueError, match="invalid:scope"):
        validate_scopes_for_role(["invalid:scope"], UserRole.ADMIN)


def test_validate_scopes_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        validate_scopes_for_role([], UserRole.ADMIN)
