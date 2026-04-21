from dataclasses import dataclass, field
from typing import Callable

from backend.config import Settings, ProviderSettings


@dataclass
class ProviderConfig:
    protocol: str  # "oidc" or "oauth2"
    label: str
    scopes: str
    # For OIDC providers, discovery_url is used to fetch endpoints at startup.
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


PROVIDERS: dict[str, ProviderConfig] = {
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
    "oidc": ProviderConfig(
        protocol="oidc",
        label="SSO",
        scopes="openid email profile",
        # discovery_url is empty — populated from settings.auth_oidc.issuer_url at runtime
        extract_subject=_oidc_extract_subject,
        extract_name=_oidc_extract_name,
        extract_email=_oidc_extract_email,
    ),
}


def get_enabled_providers(settings: Settings) -> dict[str, ProviderSettings]:
    """Return a dict of provider_name -> ProviderSettings for all enabled providers."""
    mapping = {
        "google": settings.auth_google,
        "microsoft": settings.auth_microsoft,
        "github": settings.auth_github,
        "discord": settings.auth_discord,
        "facebook": settings.auth_facebook,
        "oidc": settings.auth_oidc,
    }
    return {name: ps for name, ps in mapping.items() if ps.enabled}
