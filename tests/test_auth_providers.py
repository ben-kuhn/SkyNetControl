from backend.auth.providers import FIXED_PROVIDERS, get_enabled_providers
from backend.config import Settings, ProviderSettings, OIDCProviderConfig


def test_all_providers_defined():
    expected = {"google", "microsoft", "github", "discord", "facebook"}
    assert set(FIXED_PROVIDERS.keys()) == expected


def test_oidc_providers_have_discovery_url():
    for name, config in FIXED_PROVIDERS.items():
        if config.protocol == "oidc":
            assert config.discovery_url, f"{name} missing discovery_url"


def test_oauth2_providers_have_hardcoded_urls():
    for name, config in FIXED_PROVIDERS.items():
        if config.protocol == "oauth2":
            assert config.authorize_url, f"{name} missing authorize_url"
            assert config.token_url, f"{name} missing token_url"
            assert config.userinfo_url, f"{name} missing userinfo_url"


def test_all_providers_have_extract_functions():
    for name, config in FIXED_PROVIDERS.items():
        assert callable(config.extract_subject), f"{name} missing extract_subject"
        assert callable(config.extract_name), f"{name} missing extract_name"
        assert callable(config.extract_email), f"{name} missing extract_email"


def test_google_extract_subject():
    config = FIXED_PROVIDERS["google"]
    assert config.extract_subject({"sub": "12345"}) == "12345"


def test_github_extract_subject():
    config = FIXED_PROVIDERS["github"]
    assert config.extract_subject({"id": 42}) == "42"


def test_github_extract_name():
    config = FIXED_PROVIDERS["github"]
    assert config.extract_name({"name": "Test User"}) == "Test User"
    assert config.extract_name({"login": "testuser"}) == "testuser"


def test_facebook_extract_subject():
    config = FIXED_PROVIDERS["facebook"]
    assert config.extract_subject({"id": "999"}) == "999"


def test_discord_extract_subject():
    config = FIXED_PROVIDERS["discord"]
    assert config.extract_subject({"id": "123456"}) == "123456"


def test_discord_extract_name():
    config = FIXED_PROVIDERS["discord"]
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
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik",
            enabled=True, client_id="oid", client_secret="osec",
            issuer_url="https://idp.example.com",
        )],
    )
    result = get_enabled_providers(settings)
    assert "authentik" in result
    assert result["authentik"].client_id == "oid"


def test_build_providers_returns_fixed_five_when_no_oidc() -> None:
    from backend.auth.providers import build_providers
    settings = Settings(database_url="sqlite:///")
    providers = build_providers(settings)
    assert set(providers) == {"google", "microsoft", "github", "discord", "facebook"}


def test_build_providers_adds_dynamic_oidc_entry() -> None:
    from backend.auth.providers import build_providers
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik",
            enabled=True, client_id="x", client_secret="y",
            issuer_url="https://idp.example.com",
        )],
    )
    providers = build_providers(settings)
    assert "authentik" in providers
    assert providers["authentik"].label == "Authentik"
    assert providers["authentik"].protocol == "oidc"
    assert providers["authentik"].discovery_url == "https://idp.example.com/.well-known/openid-configuration"


def test_build_providers_still_adds_disabled_oidc_to_registry() -> None:
    # The registry holds discovery info; enabled-ness is filtered separately
    # by get_enabled_providers. So a disabled OIDC provider still appears in
    # build_providers' result.
    from backend.auth.providers import build_providers
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik",
            enabled=False, issuer_url="https://idp.example.com",
        )],
    )
    providers = build_providers(settings)
    assert "authentik" in providers


def test_get_enabled_providers_excludes_disabled_oidc() -> None:
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[OIDCProviderConfig(
            slug="authentik", name="Authentik", enabled=False,
        )],
    )
    result = get_enabled_providers(settings)
    assert "authentik" not in result


def test_get_enabled_providers_multiple_oidc() -> None:
    settings = Settings(
        database_url="sqlite:///",
        auth_oidc_providers=[
            OIDCProviderConfig(slug="authentik", name="Authentik", enabled=True),
            OIDCProviderConfig(slug="keycloak", name="Keycloak", enabled=True),
        ],
    )
    result = get_enabled_providers(settings)
    assert "authentik" in result and "keycloak" in result


def test_normalise_issuer_appends_path_if_missing() -> None:
    from backend.auth.providers import _normalise_issuer
    assert _normalise_issuer("https://idp.example.com") == "https://idp.example.com/.well-known/openid-configuration"
    assert _normalise_issuer("https://idp.example.com/") == "https://idp.example.com/.well-known/openid-configuration"


def test_normalise_issuer_idempotent() -> None:
    from backend.auth.providers import _normalise_issuer
    full = "https://idp.example.com/.well-known/openid-configuration"
    assert _normalise_issuer(full) == full
