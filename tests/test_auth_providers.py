import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.auth.providers import (
    FIXED_PROVIDERS,
    build_providers,
    get_enabled_providers,
)
from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def _seed(db, slug, **kw):
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug=slug, name=kw.get("name", slug.title()),
        enabled=kw.get("enabled", True),
        client_id=kw.get("client_id", "cid"),
        client_secret=kw.get("client_secret", "csec"),
        issuer_url=kw.get("issuer_url", ""),
    ))


# --- FIXED_PROVIDERS registry tests (no DB needed) ---


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


def test_normalise_issuer_appends_path_if_missing() -> None:
    from backend.auth.providers import _normalise_issuer
    assert _normalise_issuer("https://idp.example.com") == "https://idp.example.com/.well-known/openid-configuration"
    assert _normalise_issuer("https://idp.example.com/") == "https://idp.example.com/.well-known/openid-configuration"


def test_normalise_issuer_idempotent() -> None:
    from backend.auth.providers import _normalise_issuer
    full = "https://idp.example.com/.well-known/openid-configuration"
    assert _normalise_issuer(full) == full


# --- DB-backed tests ---


def test_get_enabled_providers_reads_from_db(db):
    _seed(db, "google", client_id="goog-id")
    _seed(db, "github", enabled=False)
    enabled = get_enabled_providers(db)
    assert "google" in enabled
    assert "github" not in enabled  # disabled
    assert enabled["google"].client_id == "goog-id"


def test_get_enabled_providers_includes_custom_oidc(db):
    _seed(db, "pocketid", name="PocketID", issuer_url="https://id.example.org")
    enabled = get_enabled_providers(db)
    assert "pocketid" in enabled
    assert enabled["pocketid"].issuer_url == "https://id.example.org"


def test_build_providers_merges_db_oidc_with_fixed_registry(db):
    _seed(db, "pocketid", name="PocketID", issuer_url="https://id.example.org")
    providers = build_providers(db)
    # Fixed providers still present:
    for fixed_slug in ("google", "microsoft", "github"):
        assert fixed_slug in providers
        assert providers[fixed_slug] is FIXED_PROVIDERS[fixed_slug] or \
               providers[fixed_slug].label == FIXED_PROVIDERS[fixed_slug].label
    # Dynamic provider added:
    assert "pocketid" in providers
    assert providers["pocketid"].label == "PocketID"
    assert providers["pocketid"].discovery_url.endswith("/.well-known/openid-configuration")


def test_build_providers_returns_fixed_registry_when_db_empty(db):
    providers = build_providers(db)
    assert set(providers.keys()) == set(FIXED_PROVIDERS.keys())


def test_disabled_provider_omitted_from_enabled(db):
    _seed(db, "google", enabled=False)
    assert "google" not in get_enabled_providers(db)


def test_provider_with_empty_client_id_is_treated_as_disabled(db):
    _seed(db, "google", enabled=True, client_id="", client_secret="")
    assert "google" not in get_enabled_providers(db)
