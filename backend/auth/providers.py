from dataclasses import dataclass, field
from typing import Callable

from backend.config_mgmt.oauth import OAuthProviderConfig, list_oauth_providers


@dataclass
class ProviderConfig:
    protocol: str  # "oidc" or "oauth2"
    label: str
    scopes: str
    # For OIDC providers, discovery_url is used to fetch endpoints at login time.
    # For OAuth2 providers, authorize/token/userinfo URLs are hardcoded.
    discovery_url: str = ""
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    extract_subject: Callable[[dict], str] = field(default=lambda: (lambda d: ""))
    extract_name: Callable[[dict], str] = field(default=lambda: (lambda d: ""))
    extract_email: Callable[[dict], str] = field(default=lambda: (lambda d: ""))


def _oidc_extract_subject(data: dict) -> str:
    return str(data.get("sub", ""))


def _oidc_extract_name(data: dict) -> str:
    return data.get("name", data.get("preferred_username", "Unknown"))


def _oidc_extract_email(data: dict) -> str:
    # Trust the IdP-provided email only when it is marked verified. An OIDC
    # provider that lets users self-serve account creation with arbitrary
    # email (and never verifies) could let an attacker claim a victim's
    # email — innocuous for sign-in (we key on `oidc_subject`, not email)
    # but the unverified email would still be stored on the User row and
    # used by admin contact features. Per the spec, `email_verified` is a
    # boolean; if absent or false, treat the email as unknown.
    if not data.get("email_verified"):
        return ""
    return data.get("email", "")


def _github_extract_subject(data: dict) -> str:
    return str(data.get("id", ""))


def _github_extract_name(data: dict) -> str:
    return data.get("name", data.get("login", "Unknown"))


def _github_extract_email(data: dict) -> str:
    return data.get("email", "")


def _discord_extract_subject(data: dict) -> str:
    return str(data.get("id", ""))


def _discord_extract_name(data: dict) -> str:
    return data.get("username", "Unknown")


def _discord_extract_email(data: dict) -> str:
    return data.get("email", "")


def _facebook_extract_subject(data: dict) -> str:
    return str(data.get("id", ""))


def _facebook_extract_name(data: dict) -> str:
    return data.get("name", "Unknown")


def _facebook_extract_email(data: dict) -> str:
    return data.get("email", "")


FIXED_PROVIDERS: dict[str, ProviderConfig] = {
    "google": ProviderConfig(
        protocol="oidc",
        label="Google",
        scopes="openid email profile",
        discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        extract_subject=_oidc_extract_subject,
        extract_name=_oidc_extract_name,
        extract_email=_oidc_extract_email,
    ),
    "microsoft": ProviderConfig(
        protocol="oidc",
        label="Microsoft",
        scopes="openid email profile",
        discovery_url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
        extract_subject=_oidc_extract_subject,
        extract_name=_oidc_extract_name,
        extract_email=_oidc_extract_email,
    ),
    "github": ProviderConfig(
        protocol="oauth2",
        label="GitHub",
        scopes="read:user user:email",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        extract_subject=_github_extract_subject,
        extract_name=_github_extract_name,
        extract_email=_github_extract_email,
    ),
    "discord": ProviderConfig(
        protocol="oauth2",
        label="Discord",
        scopes="identify email",
        authorize_url="https://discord.com/api/oauth2/authorize",
        token_url="https://discord.com/api/oauth2/token",
        userinfo_url="https://discord.com/api/users/@me",
        extract_subject=_discord_extract_subject,
        extract_name=_discord_extract_name,
        extract_email=_discord_extract_email,
    ),
    "facebook": ProviderConfig(
        protocol="oauth2",
        label="Facebook",
        scopes="public_profile email",
        authorize_url="https://www.facebook.com/v19.0/dialog/oauth",
        token_url="https://graph.facebook.com/v19.0/oauth/access_token",
        userinfo_url="https://graph.facebook.com/v19.0/me?fields=id,name,email",
        extract_subject=_facebook_extract_subject,
        extract_name=_facebook_extract_name,
        extract_email=_facebook_extract_email,
    ),
}


def _normalise_issuer(url: str) -> str:
    """Return the OIDC discovery URL — append /.well-known/openid-configuration if not already present."""
    url = url.rstrip("/")
    if url.endswith("/.well-known/openid-configuration"):
        return url
    return f"{url}/.well-known/openid-configuration"


def build_providers(db) -> dict[str, ProviderConfig]:
    """Return all known providers — the fixed registry plus any custom OIDC
    providers configured in the AppConfig table — keyed by slug.

    Disabled providers and fixed providers without DB rows still appear; the
    set of *enabled* providers is exposed by `get_enabled_providers`.
    """
    result = dict(FIXED_PROVIDERS)
    for p in list_oauth_providers(db):
        if p.slug in FIXED_PROVIDERS:
            continue  # the fixed registry already has the right ProviderConfig
        result[p.slug] = ProviderConfig(
            protocol="oidc",
            label=p.name or p.slug.title(),
            scopes="openid email profile",
            discovery_url=_normalise_issuer(p.issuer_url) if p.issuer_url else "",
            extract_subject=_oidc_extract_subject,
            extract_name=_oidc_extract_name,
            extract_email=_oidc_extract_email,
        )
    return result


def get_enabled_providers(db) -> dict[str, OAuthProviderConfig]:
    """Return enabled providers keyed by slug.

    A provider is *enabled* if its DB row has enabled=true AND a non-empty
    client_id. The client_id check matches the previous Pydantic behaviour
    where a provider with no credentials was effectively unusable.
    """
    enabled: dict[str, OAuthProviderConfig] = {}
    for p in list_oauth_providers(db):
        if p.enabled and p.client_id:
            enabled[p.slug] = p
    return enabled
