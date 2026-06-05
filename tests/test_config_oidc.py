import pytest
from pydantic import ValidationError

from backend.config import Settings


def test_no_oidc_env_means_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # Strip any SKYNET_AUTH_OIDC_* the host environment might have set.
    for key in list(os_environ_keys()):
        if key.startswith("SKYNET_AUTH_OIDC_") and key not in {
            "SKYNET_AUTH_OIDC_ENABLED",
            "SKYNET_AUTH_OIDC_CLIENT_ID",
            "SKYNET_AUTH_OIDC_CLIENT_SECRET",
            "SKYNET_AUTH_OIDC_ISSUER_URL",
        }:
            monkeypatch.delenv(key, raising=False)
    settings = Settings(database_url="sqlite:///")
    assert settings.auth_oidc_providers == []


def os_environ_keys():
    import os
    return list(os.environ.keys())


def test_oidc_providers_parsed_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_ENABLED", "true")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_NAME", "Authentik")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_CLIENT_ID", "client-a")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_CLIENT_SECRET", "secret-a")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_ISSUER_URL", "https://idp.example.com")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_ENABLED", "false")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_NAME", "Keycloak")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_CLIENT_ID", "client-k")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_CLIENT_SECRET", "secret-k")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_KEYCLOAK_ISSUER_URL", "https://kc.example.com")

    settings = Settings(database_url="sqlite:///")

    by_slug = {p.slug: p for p in settings.auth_oidc_providers}
    assert set(by_slug) == {"authentik", "keycloak"}
    a = by_slug["authentik"]
    assert a.name == "Authentik"
    assert a.enabled is True
    assert a.client_id == "client-a"
    assert a.client_secret == "secret-a"
    assert a.issuer_url == "https://idp.example.com"
    k = by_slug["keycloak"]
    assert k.enabled is False


def test_oidc_provider_missing_name_defaults_to_titlecased_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYNET_AUTH_OIDC_AUTHENTIK_ISSUER_URL", "https://idp.example.com")
    settings = Settings(database_url="sqlite:///")
    by_slug = {p.slug: p for p in settings.auth_oidc_providers}
    assert by_slug["authentik"].name == "Authentik"


def test_oidc_dashed_slug_via_underscored_env_middle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYNET_AUTH_OIDC_MY_IDP_ENABLED", "true")
    monkeypatch.setenv("SKYNET_AUTH_OIDC_MY_IDP_CLIENT_ID", "x")
    settings = Settings(database_url="sqlite:///")
    slugs = [p.slug for p in settings.auth_oidc_providers]
    assert "my-idp" in slugs


@pytest.mark.parametrize("reserved_middle", ["GOOGLE", "GITHUB", "MICROSOFT", "DISCORD", "FACEBOOK"])
def test_reserved_slug_rejected_at_startup(monkeypatch: pytest.MonkeyPatch, reserved_middle: str) -> None:
    monkeypatch.setenv(f"SKYNET_AUTH_OIDC_{reserved_middle}_ENABLED", "true")
    monkeypatch.setenv(f"SKYNET_AUTH_OIDC_{reserved_middle}_CLIENT_ID", "x")
    with pytest.raises(ValidationError) as exc_info:
        Settings(database_url="sqlite:///")
    msg = str(exc_info.value)
    assert "reserved" in msg
    assert f"SKYNET_AUTH_OIDC_{reserved_middle}_" in msg
