from backend.auth.providers import PROVIDERS, get_enabled_providers
from backend.config import Settings, ProviderSettings, OIDCProviderSettings


def test_all_providers_defined():
    expected = {"google", "microsoft", "github", "discord", "facebook", "oidc"}
    assert set(PROVIDERS.keys()) == expected


def test_oidc_providers_have_discovery_url():
    for name, config in PROVIDERS.items():
        if config.protocol == "oidc":
            assert config.discovery_url or name == "oidc", f"{name} missing discovery_url"


def test_oauth2_providers_have_hardcoded_urls():
    for name, config in PROVIDERS.items():
        if config.protocol == "oauth2":
            assert config.authorize_url, f"{name} missing authorize_url"
            assert config.token_url, f"{name} missing token_url"
            assert config.userinfo_url, f"{name} missing userinfo_url"


def test_all_providers_have_extract_functions():
    for name, config in PROVIDERS.items():
        assert callable(config.extract_subject), f"{name} missing extract_subject"
        assert callable(config.extract_name), f"{name} missing extract_name"
        assert callable(config.extract_email), f"{name} missing extract_email"


def test_google_extract_subject():
    config = PROVIDERS["google"]
    assert config.extract_subject({"sub": "12345"}) == "12345"


def test_github_extract_subject():
    config = PROVIDERS["github"]
    assert config.extract_subject({"id": 42}) == "42"


def test_github_extract_name():
    config = PROVIDERS["github"]
    assert config.extract_name({"name": "Test User"}) == "Test User"
    assert config.extract_name({"login": "testuser"}) == "testuser"


def test_facebook_extract_subject():
    config = PROVIDERS["facebook"]
    assert config.extract_subject({"id": "999"}) == "999"


def test_discord_extract_subject():
    config = PROVIDERS["discord"]
    assert config.extract_subject({"id": "123456"}) == "123456"


def test_discord_extract_name():
    config = PROVIDERS["discord"]
    assert config.extract_name({"username": "testuser"}) == "testuser"


def test_get_enabled_providers_none_enabled():
    settings = Settings(database_url="sqlite:///")
    result = get_enabled_providers(settings)
    assert result == {}


def test_get_enabled_providers_google_enabled():
    settings = Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
    )
    result = get_enabled_providers(settings)
    assert "google" in result
    assert result["google"].client_id == "gid"


def test_get_enabled_providers_oidc_enabled():
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc=OIDCProviderSettings(
            enabled=True, client_id="oid", client_secret="osec", issuer_url="https://idp.example.com"
        ),
    )
    result = get_enabled_providers(settings)
    assert "oidc" in result
    assert result["oidc"].client_id == "oid"
