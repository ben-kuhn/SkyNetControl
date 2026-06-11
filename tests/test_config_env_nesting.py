"""Regression tests for env-var → Settings nested-field population.

The original `env_nested_delimiter = "_"` silently dropped subfields whose
names contained an underscore (`client_id`, `from_address`), so a NixOS
deployment with `SKYNET_AUTH_GITHUB_CLIENT_ID=...` ended up with an empty
`client_id` and the provider redirected to a malformed OAuth URL.

The fix is `env_nested_delimiter = "__"` for fixed providers and SMTP.
Dynamic OIDC providers continue to use a single underscore because they
are parsed by a custom regex, not by pydantic-settings nesting.
"""
import pytest

from backend.config import Settings


def _strip_skynet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    for key in list(os.environ):
        if key.startswith("SKYNET_"):
            monkeypatch.delenv(key, raising=False)


def test_fixed_provider_double_underscore_populates_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strip_skynet_env(monkeypatch)
    monkeypatch.setenv("SKYNET_AUTH_GITHUB__ENABLED", "true")
    monkeypatch.setenv("SKYNET_AUTH_GITHUB__CLIENT_ID", "Iv1.abc")
    monkeypatch.setenv("SKYNET_AUTH_GITHUB__CLIENT_SECRET", "ghs_xyz")

    s = Settings()

    assert s.auth_github.enabled is True
    assert s.auth_github.client_id == "Iv1.abc"
    assert s.auth_github.client_secret == "ghs_xyz"


@pytest.mark.parametrize("attr,prefix", [
    ("auth_google", "SKYNET_AUTH_GOOGLE__"),
    ("auth_microsoft", "SKYNET_AUTH_MICROSOFT__"),
    ("auth_discord", "SKYNET_AUTH_DISCORD__"),
    ("auth_facebook", "SKYNET_AUTH_FACEBOOK__"),
])
def test_each_fixed_provider_loads_credentials_from_env(
    monkeypatch: pytest.MonkeyPatch, attr: str, prefix: str,
) -> None:
    _strip_skynet_env(monkeypatch)
    monkeypatch.setenv(f"{prefix}ENABLED", "true")
    monkeypatch.setenv(f"{prefix}CLIENT_ID", "id-value")
    monkeypatch.setenv(f"{prefix}CLIENT_SECRET", "secret-value")

    s = Settings()
    provider = getattr(s, attr)

    assert provider.enabled is True
    assert provider.client_id == "id-value"
    assert provider.client_secret == "secret-value"


def test_smtp_double_underscore_populates_multi_word_subfields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strip_skynet_env(monkeypatch)
    monkeypatch.setenv("SKYNET_SMTP__HOST", "smtp.example.com")
    monkeypatch.setenv("SKYNET_SMTP__PORT", "465")
    monkeypatch.setenv("SKYNET_SMTP__USERNAME", "user@example.com")
    monkeypatch.setenv("SKYNET_SMTP__PASSWORD", "hunter2")
    monkeypatch.setenv("SKYNET_SMTP__USE_TLS", "false")
    monkeypatch.setenv("SKYNET_SMTP__FROM_ADDRESS", "net@example.com")

    s = Settings()

    assert s.smtp.host == "smtp.example.com"
    assert s.smtp.port == 465
    assert s.smtp.username == "user@example.com"
    assert s.smtp.password == "hunter2"
    assert s.smtp.use_tls is False
    assert s.smtp.from_address == "net@example.com"


def test_single_underscore_does_not_populate_underscore_named_subfields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Document the original bug: single-underscore env vars silently fail
    for subfields whose own name contains an underscore. If pydantic-settings
    one day learns to disambiguate this, we can drop the double-underscore
    convention — but until then, this test guards the convention."""
    _strip_skynet_env(monkeypatch)
    monkeypatch.setenv("SKYNET_AUTH_GITHUB_CLIENT_ID", "should-not-be-picked-up")
    monkeypatch.setenv("SKYNET_SMTP_FROM_ADDRESS", "should-not-be-picked-up")

    s = Settings()

    assert s.auth_github.client_id == ""
    assert s.smtp.from_address == ""
