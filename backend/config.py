from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///skynetcontrol.db"
    static_dir: str = "frontend/dist"
    debug: bool = False

    # OIDC
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # App
    app_base_url: str = "http://localhost:8000"

    model_config = {"env_prefix": "SKYNET_"}


settings = Settings()
