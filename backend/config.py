from pydantic import BaseModel
from pydantic_settings import BaseSettings


class ProviderSettings(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""


class OIDCProviderSettings(ProviderSettings):
    issuer_url: str = ""


class SmtpSettings(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    from_address: str = ""


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
    auth_oidc: OIDCProviderSettings = OIDCProviderSettings()

    # SMTP
    smtp: SmtpSettings = SmtpSettings()

    model_config = {"env_prefix": "SKYNET_", "env_nested_delimiter": "_"}


settings = Settings()
