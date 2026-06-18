import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from unittest.mock import patch, AsyncMock

from backend.auth.service import resolve_provider, _DISCOVERY_CACHE
from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider
from backend.db.base import Base


@pytest.fixture(autouse=True)
def clear_discovery_cache():
    _DISCOVERY_CACHE.clear()
    yield
    _DISCOVERY_CACHE.clear()


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.mark.asyncio
async def test_resolve_provider_returns_none_when_not_enabled(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=False,
        client_id="c", client_secret="s", issuer_url="",
    ))
    assert await resolve_provider(db, "google") is None


@pytest.mark.asyncio
async def test_resolve_provider_unknown_slug_returns_none(db):
    assert await resolve_provider(db, "nonexistent") is None


@pytest.mark.asyncio
async def test_resolve_provider_oauth2_no_discovery(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="github", name="GitHub", enabled=True,
        client_id="ghc", client_secret="ghs", issuer_url="",
    ))
    resolved = await resolve_provider(db, "github")
    assert resolved is not None
    assert resolved["client_id"] == "ghc"
    assert resolved["protocol"] == "oauth2"
    assert resolved["authorize_url"].startswith("https://github.com")


@pytest.mark.asyncio
async def test_resolve_provider_oidc_fetches_discovery(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=True,
        client_id="gc", client_secret="gs", issuer_url="",
    ))
    with patch("backend.auth.service.fetch_oidc_discovery",
               new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {
            "authorization_endpoint": "https://x/auth",
            "token_endpoint": "https://x/token",
            "userinfo_endpoint": "https://x/userinfo",
        }
        resolved = await resolve_provider(db, "google")
    assert resolved is not None
    assert resolved["authorize_url"] == "https://x/auth"


@pytest.mark.asyncio
async def test_resolve_provider_caches_oidc_discovery(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=True,
        client_id="gc", client_secret="gs", issuer_url="",
    ))
    with patch("backend.auth.service.fetch_oidc_discovery",
               new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {
            "authorization_endpoint": "https://x/auth",
            "token_endpoint": "https://x/token",
            "userinfo_endpoint": "https://x/userinfo",
        }
        await resolve_provider(db, "google")
        await resolve_provider(db, "google")
    assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_resolve_provider_returns_none_when_discovery_fails(db):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=True,
        client_id="gc", client_secret="gs", issuer_url="",
    ))
    with patch("backend.auth.service.fetch_oidc_discovery",
               new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = None
        assert await resolve_provider(db, "google") is None


# Keep the fetch_oidc_discovery tests from the old file — these are still valid

@pytest.mark.asyncio
async def test_fetch_oidc_discovery_success():
    from unittest.mock import MagicMock
    import backend.auth.service as svc

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "authorization_endpoint": "https://example.com/authorize",
        "token_endpoint": "https://example.com/token",
        "userinfo_endpoint": "https://example.com/userinfo",
    }

    with patch("backend.auth.service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await svc.fetch_oidc_discovery("https://example.com/.well-known/openid-configuration")

    assert result["authorization_endpoint"] == "https://example.com/authorize"
    assert result["token_endpoint"] == "https://example.com/token"
    assert result["userinfo_endpoint"] == "https://example.com/userinfo"


@pytest.mark.asyncio
async def test_fetch_oidc_discovery_rejects_http_scheme():
    """SSRF guard: non-https issuer URLs must be refused before any fetch."""
    import backend.auth.service as svc

    with patch("backend.auth.service.httpx.AsyncClient") as mock_client_cls:
        result = await svc.fetch_oidc_discovery("http://example.com/.well-known/openid-configuration")

    assert result is None
    # Crucially: no HTTP fetch was even attempted.
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_oidc_discovery_rejects_loopback_host():
    """SSRF guard: localhost / 127.x must not be fetchable as an OIDC issuer."""
    import backend.auth.service as svc

    with patch("backend.auth.service.httpx.AsyncClient") as mock_client_cls:
        result = await svc.fetch_oidc_discovery("https://127.0.0.1/.well-known/openid-configuration")

    assert result is None
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_oidc_discovery_rejects_link_local_metadata_ip():
    """SSRF guard: AWS metadata IP 169.254.169.254 (link-local) must be refused."""
    import backend.auth.service as svc

    with patch("backend.auth.service.httpx.AsyncClient") as mock_client_cls:
        result = await svc.fetch_oidc_discovery("https://169.254.169.254/.well-known/openid-configuration")

    assert result is None
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_oidc_discovery_rejects_private_rfc1918():
    """SSRF guard: 10.x / 192.168.x / 172.16-31.x must be refused."""
    import backend.auth.service as svc

    for host in ("10.0.0.1", "192.168.1.1", "172.20.0.1"):
        with patch("backend.auth.service.httpx.AsyncClient") as mock_client_cls:
            result = await svc.fetch_oidc_discovery(f"https://{host}/.well-known/openid-configuration")
        assert result is None
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_oidc_discovery_failure_returns_none():
    import backend.auth.service as svc

    with patch("backend.auth.service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await svc.fetch_oidc_discovery("https://bad.example.com/.well-known/openid-configuration")

    assert result is None
