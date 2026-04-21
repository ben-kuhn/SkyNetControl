import pytest

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


def test_create_and_decode_token(auth_settings):
    token = create_access_token(
        callsign="W0NE",
        role="admin",
        settings=auth_settings,
    )
    assert isinstance(token, str)

    payload = decode_access_token(token, settings=auth_settings)
    assert payload is not None
    assert payload["sub"] == "W0NE"
    assert payload["role"] == "admin"


def test_decode_invalid_token(auth_settings):
    payload = decode_access_token("invalid.token.here", settings=auth_settings)
    assert payload is None


def test_decode_wrong_secret(auth_settings):
    token = create_access_token(
        callsign="W0NE",
        role="admin",
        settings=auth_settings,
    )
    wrong_settings = Settings(
        database_url="sqlite:///",
        jwt_secret_key="wrong-secret",
    )
    payload = decode_access_token(token, settings=wrong_settings)
    assert payload is None
