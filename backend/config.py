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

    # AES-GCM key material for the at-rest secret box (see auth/secret_box.py).
    # When empty, falls back to jwt_secret_key — convenient for installs that
    # don't care about independent rotation. Setting this lets operators
    # rotate the JWT signing key without invalidating encrypted OAuth/SMTP
    # credentials, and vice versa.
    secrets_key: str = ""

    # App
    app_base_url: str = "http://localhost:8000"

    # Comma-separated IP allowlist for proxies whose Forwarded headers we
    # trust. Empty (the default) means "don't trust any" — request.client.host
    # is taken verbatim. When set, the rate limiter accepts the right-most
    # value of `CF-Connecting-IP` / `X-Real-IP` / `X-Forwarded-For` as the
    # true client IP IF the connecting peer is in this list. Typical value
    # for a Cloudflare deployment: "127.0.0.1,::1" (the cloudflared sidecar
    # / cloudflare tunnel endpoint). Operators behind a different proxy
    # populate this with the proxy's egress IP(s).
    trusted_proxies: str = ""

    model_config = {"env_prefix": "SKYNET_"}


settings = Settings()
