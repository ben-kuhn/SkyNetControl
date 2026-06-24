import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from backend.auth.rate_limit import reset_for_tests as _reset_rate_limit
from backend.auth.secret_box import install_key_material
from backend.config import Settings
from backend.db.base import Base


# Many tests construct routes / call upsert_oauth_provider without going
# through create_app, so the secret_box key isn't bound. Install a fixed
# test key once at session start — encrypt/decrypt are symmetric within a
# single process, so the actual material doesn't matter as long as it's
# consistent across the whole run.
install_key_material("test-secret")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The in-memory per-IP rate limiter is process-global. Without a
    reset between tests, requests from the synthetic test client at
    127.0.0.1 share state across the whole suite — and a stress test
    file can starve every subsequent test of its quota."""
    _reset_rate_limit()
    yield
    _reset_rate_limit()


@pytest.fixture(autouse=True)
def _stub_nominatim(monkeypatch):
    """No test should make a real HTTP call to nominatim.openstreetmap.org.
    Default to returning None (no match) so the city/state fallback in
    `_compute_checkin_fields` becomes a no-op. Tests that specifically
    exercise the geocoder cache override this with their own monkeypatch."""
    from backend.integrations.geocoder import service as geocoder_service
    monkeypatch.setattr(geocoder_service, "_call_nominatim", lambda *a, **kw: None)


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", debug=True, jwt_secret_key="test-secret")


@pytest.fixture
def app(test_settings):
    application = create_app(settings=test_settings)
    # Create all tables for tests
    Base.metadata.create_all(application.state.engine)
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def seed_oauth_provider():
    """Factory that writes an OAuth provider row given a db session.

    Usage:
        def test_x(db_session, seed_oauth_provider):
            seed_oauth_provider(db_session, "google", client_id="cid", client_secret="csec")
    """
    from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider

    def _seed(db, slug: str, **overrides):
        upsert_oauth_provider(db, OAuthProviderConfig(
            slug=slug,
            name=overrides.get("name", slug.title()),
            enabled=overrides.get("enabled", True),
            client_id=overrides.get("client_id", "test-cid"),
            client_secret=overrides.get("client_secret", "test-csec"),
            issuer_url=overrides.get("issuer_url", ""),
        ))
    return _seed
