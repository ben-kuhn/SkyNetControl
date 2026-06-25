import pytest

from backend.auth.models import User
from backend.auth.service import create_access_token, decode_access_token
from backend.config import Settings


@pytest.fixture
def auth_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_algorithm="HS256",
        jwt_expire_minutes=60,
    )


def _make_user(callsign: str, is_admin: bool = False, token_version: int = 0):
    """Build a transient User stub for JWT minting (no DB required)."""
    from types import SimpleNamespace
    return SimpleNamespace(
        callsign=callsign,
        is_admin=is_admin,
        is_pending=False,
        token_version=token_version,
    )


def test_create_and_decode_token(auth_settings):
    user = _make_user("W0NE", is_admin=True)
    token = create_access_token(user, auth_settings)
    assert isinstance(token, str)

    payload = decode_access_token(token, settings=auth_settings)
    assert payload is not None
    assert payload["sub"] == "W0NE"
    assert payload["is_admin"] is True


def test_decode_invalid_token(auth_settings):
    payload = decode_access_token("invalid.token.here", settings=auth_settings)
    assert payload is None


def test_decode_wrong_secret(auth_settings):
    user = _make_user("W0NE", is_admin=True)
    token = create_access_token(user, auth_settings)
    wrong_settings = Settings(
        database_url="sqlite:///",
        jwt_secret_key="wrong-secret",
    )
    payload = decode_access_token(token, settings=wrong_settings)
    assert payload is None


def test_jwt_carries_is_admin():
    from backend.config import Settings
    settings = Settings(jwt_secret_key="x" * 32)
    user = _make_user("W0ADM", is_admin=True)
    tok = create_access_token(user, settings)
    payload = decode_access_token(tok, settings=settings)
    assert payload["is_admin"] is True
