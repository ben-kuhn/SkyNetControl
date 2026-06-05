import os
import re

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings

from backend.auth.oidc_slug import slug_from_env_middle, validate_slug


class ProviderSettings(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""


class OIDCProviderSettings(ProviderSettings):
    issuer_url: str = ""


class OIDCProviderConfig(BaseModel):
    slug: str
    name: str
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    issuer_url: str = ""


class SmtpSettings(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    from_address: str = ""


_OIDC_ENV_RE = re.compile(
    r"^SKYNET_AUTH_OIDC_([A-Z0-9_]+)_(NAME|ENABLED|CLIENT_ID|CLIENT_SECRET|ISSUER_URL)$"
)


class Settings(BaseSettings):
    database_url: str = "sqlite:///skynetcontrol.db"
    static_dir: str = "frontend/dist"
    debug: bool = False

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # App
    app_base_url: str = "http://localhost:8000"

    # Auth providers
    auth_google: ProviderSettings = ProviderSettings()
    auth_microsoft: ProviderSettings = ProviderSettings()
    auth_github: ProviderSettings = ProviderSettings()
    auth_discord: ProviderSettings = ProviderSettings()
    auth_facebook: ProviderSettings = ProviderSettings()
    auth_oidc: OIDCProviderSettings = OIDCProviderSettings()  # removed in Task 3
    auth_oidc_providers: list[OIDCProviderConfig] = []

    # SMTP
    smtp: SmtpSettings = SmtpSettings()

    model_config = {"env_prefix": "SKYNET_", "env_nested_delimiter": "_"}

    @model_validator(mode="before")
    @classmethod
    def _gather_oidc_providers(cls, data):
        # Scan os.environ and build auth_oidc_providers from SKYNET_AUTH_OIDC_*
        # env vars. Explicit kwarg `auth_oidc_providers=[...]` (used in tests)
        # always wins to keep tests deterministic regardless of host env state.
        if not isinstance(data, dict):
            return data
        if data.get("auth_oidc_providers"):
            return data
        groups: dict[str, dict[str, str]] = {}
        for key, value in os.environ.items():
            m = _OIDC_ENV_RE.match(key)
            if not m:
                continue
            middle, field = m.group(1), m.group(2)
            groups.setdefault(middle, {})[field.lower()] = value
        providers = []
        for middle in sorted(groups):
            slug = slug_from_env_middle(middle)
            err = validate_slug(slug)
            if err:
                raise ValueError(
                    f"Invalid OIDC slug derived from env var SKYNET_AUTH_OIDC_{middle}_*: {err}"
                )
            fields = groups[middle]
            providers.append({
                "slug": slug,
                "name": fields.get("name") or slug.title(),
                "enabled": fields.get("enabled", "false").lower() == "true",
                "client_id": fields.get("client_id", ""),
                "client_secret": fields.get("client_secret", ""),
                "issuer_url": fields.get("issuer_url", ""),
            })
        if providers:
            data["auth_oidc_providers"] = providers
        return data


settings = Settings()
