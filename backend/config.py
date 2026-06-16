"""Process-level configuration loaded from env vars.

After Phase 5 of the config-unification effort, this module is intentionally
tiny: everything that can live in the AppConfig table does. Only knobs that
must be available before the first DB read (database URL, JWT signing key,
the base URL the OAuth providers use as a redirect target) stay here.
"""
from pydantic_settings import BaseSettings


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

    model_config = {"env_prefix": "SKYNET_"}


settings = Settings()
