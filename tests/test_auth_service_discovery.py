import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from backend.auth.service import fetch_oidc_discovery, init_providers
from backend.config import Settings, ProviderSettings, OIDCProviderConfig


@pytest.mark.asyncio
async def test_fetch_oidc_discovery_success():
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

        result = await fetch_oidc_discovery("https://example.com/.well-known/openid-configuration")

    assert result["authorization_endpoint"] == "https://example.com/authorize"
    assert result["token_endpoint"] == "https://example.com/token"
    assert result["userinfo_endpoint"] == "https://example.com/userinfo"


@pytest.mark.asyncio
async def test_fetch_oidc_discovery_failure_returns_none():
    with patch("backend.auth.service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fetch_oidc_discovery("https://bad.example.com/.well-known/openid-configuration")

    assert result is None


@pytest.mark.asyncio
async def test_init_providers_with_google():
    settings = Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
    )

    mock_discovery = {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    }

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock, return_value=mock_discovery):
        providers = await init_providers(settings)

    assert "google" in providers
    assert providers["google"]["authorize_url"] == "https://accounts.google.com/o/oauth2/v2/auth"
    assert providers["google"]["client_id"] == "gid"
    assert providers["google"]["client_secret"] == "gsec"


@pytest.mark.asyncio
async def test_init_providers_with_github():
    settings = Settings(
        database_url="sqlite:///",
        auth_github=ProviderSettings(enabled=True, client_id="ghid", client_secret="ghsec"),
    )

    providers = await init_providers(settings)

    assert "github" in providers
    assert providers["github"]["authorize_url"] == "https://github.com/login/oauth/authorize"
    assert providers["github"]["client_id"] == "ghid"


@pytest.mark.asyncio
async def test_init_providers_with_generic_oidc():
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik",
            enabled=True, client_id="oid", client_secret="osec",
            issuer_url="https://idp.example.com",
        )],
    )

    mock_discovery = {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
        "userinfo_endpoint": "https://idp.example.com/userinfo",
    }

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock, return_value=mock_discovery):
        providers = await init_providers(settings)

    assert "authentik" in providers
    assert providers["authentik"]["authorize_url"] == "https://idp.example.com/authorize"


@pytest.mark.asyncio
async def test_init_providers_skips_failed_oidc_discovery():
    settings = Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
        auth_github=ProviderSettings(enabled=True, client_id="ghid", client_secret="ghsec"),
    )

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock, return_value=None):
        providers = await init_providers(settings)

    # Google (OIDC) should be skipped, GitHub (OAuth2) should still be there
    assert "google" not in providers
    assert "github" in providers


@pytest.mark.asyncio
async def test_init_providers_none_enabled_raises():
    settings = Settings(database_url="sqlite:///")

    with pytest.raises(RuntimeError, match="No auth providers"):
        await init_providers(settings)


@pytest.mark.asyncio
async def test_init_providers_all_fail_raises():
    settings = Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
    )

    with patch("backend.auth.service.fetch_oidc_discovery", new_callable=AsyncMock, return_value=None):
        with pytest.raises(RuntimeError, match="No auth providers"):
            await init_providers(settings)
