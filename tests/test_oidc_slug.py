import pytest

from backend.auth import oidc_slug


@pytest.mark.parametrize("name,expected", [
    ("Authentik", "authentik"),
    ("My IdP!", "my-idp"),
    ("  spaces  ", "spaces"),
    ("Company SSO 2", "company-sso-2"),
    ("---weird---", "weird"),
])
def test_slugify(name: str, expected: str) -> None:
    assert oidc_slug.slugify(name) == expected


@pytest.mark.parametrize("slug", [
    "authentik", "company-sso", "my-idp", "a", "a1", "a-b-c", "x2",
])
def test_validate_slug_accepts_good(slug: str) -> None:
    assert oidc_slug.validate_slug(slug) is None


@pytest.mark.parametrize("slug,reason_fragment", [
    ("", "must be lowercase"),
    ("-foo", "must be lowercase"),
    ("foo-", "must be lowercase"),
    ("foo--bar", "must be lowercase"),
    ("FOO", "must be lowercase"),
    ("foo_bar", "must be lowercase"),
    ("foo bar", "must be lowercase"),
])
def test_validate_slug_rejects_malformed(slug: str, reason_fragment: str) -> None:
    err = oidc_slug.validate_slug(slug)
    assert err is not None
    assert reason_fragment in err


@pytest.mark.parametrize("slug", ["google", "github", "microsoft", "discord", "facebook", "oidc"])
def test_validate_slug_rejects_reserved(slug: str) -> None:
    err = oidc_slug.validate_slug(slug)
    assert err is not None
    assert "reserved" in err


@pytest.mark.parametrize("middle,slug", [
    ("AUTHENTIK", "authentik"),
    ("MY_IDP", "my-idp"),
    ("COMPANY_SSO_2", "company-sso-2"),
])
def test_slug_from_env_middle(middle: str, slug: str) -> None:
    assert oidc_slug.slug_from_env_middle(middle) == slug


@pytest.mark.parametrize("slug,middle", [
    ("authentik", "AUTHENTIK"),
    ("my-idp", "MY_IDP"),
    ("company-sso-2", "COMPANY_SSO_2"),
])
def test_env_middle_from_slug(slug: str, middle: str) -> None:
    assert oidc_slug.env_middle_from_slug(slug) == middle
