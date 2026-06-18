import asyncio
import base64
import hashlib
import os
import re
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.config import Settings, settings as default_settings
from backend.db.session import create_engine_from_url, create_session_factory
from backend.auth.routes import auth_router
from backend.auth.pat_routes import pat_router
from backend.config_mgmt.routes import config_router
from backend.modules.schedule.routes import schedule_router
from backend.modules.activities.routes import activities_router
from backend.modules.checkins.routes import checkins_router
from backend.modules.reminders.routes import reminders_router
from backend.modules.roster.routes import roster_router
from backend.modules.notifications.routes import notifications_router
from backend.audit.routes import audit_router
from backend.integrations.delivery.routes import delivery_router
from backend.integrations.scanner.routes import scanner_router
from backend.privacy.routes import privacy_router
from backend.config_mgmt.oauth_routes import oauth_router
from backend.config_mgmt.smtp_routes import smtp_router
from backend.config_mgmt.test_routes import test_router
from backend.config_mgmt.setup_routes import setup_router
from backend.auth.recovery_routes import recovery_router


_DEFAULT_JWT_SECRET = "change-me-in-production"


# Inline <script>...</script> blocks (no src=) in served HTML. Vite emits
# index.html with a tiny theme-bootstrap snippet that reads localStorage
# and sets data-theme before React mounts. CSP would block it without an
# 'unsafe-inline' allowance (which defeats most of the point) — so we
# hash each inline block at startup and emit script-src 'sha256-...'
# instead. Matches what browsers expect per W3C CSP §6.6.4.
_INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)


def _csp_script_hashes(static_dir: str) -> list[str]:
    """Return CSP `'sha256-…'` literals for every inline script in index.html.

    Empty list when no index.html is served — keeps script-src 'self'
    permissive enough for the API-only deployments (tests) where the
    SPA isn't bundled.
    """
    index_path = os.path.join(static_dir, "index.html")
    if not os.path.isfile(index_path):
        return []
    with open(index_path, "rb") as f:
        html = f.read().decode("utf-8", errors="replace")
    hashes = []
    for match in _INLINE_SCRIPT_RE.finditer(html):
        body = match.group(1).encode("utf-8")
        digest = hashlib.sha256(body).digest()
        hashes.append(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")
    return hashes


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings

    if settings.jwt_secret_key == _DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "SKYNET_JWT_SECRET_KEY is still the default placeholder value. "
            "Generate one with `openssl rand -hex 32` and set it via the "
            "SKYNET_JWT_SECRET_KEY env var before starting the server."
        )

    # Bind the AEAD key used by backend.auth.secret_box for at-rest
    # encryption of OAuth client secrets and SMTP password in app_config.
    # Prefers a dedicated SKYNET_SECRETS_KEY when set; falls back to the
    # JWT secret so an installation that doesn't care about independent
    # rotation works with one env var. Rotating SKYNET_SECRETS_KEY without
    # SKYNET_JWT_SECRET_KEY (or vice versa) is now possible.
    from backend.auth.secret_box import install_key_material

    install_key_material(settings.secrets_key or settings.jwt_secret_key)

    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scanner_task = None
        try:
            with session_factory() as db:
                from backend.config_mgmt.service import get_config_value

                enabled = get_config_value(db, "scanner.enabled", "false")
            if enabled == "true":
                from backend.integrations.scanner.service import scanner_loop

                def get_interval():
                    with session_factory() as db:
                        from backend.config_mgmt.service import get_config_value as gcv

                        return int(gcv(db, "scanner.interval_minutes", "5"))

                scanner_task = asyncio.create_task(scanner_loop(session_factory, get_interval))
        except Exception:
            pass

        yield

        if scanner_task is not None:
            from backend.integrations.scanner.service import scanner_state

            scanner_state.running = False
            scanner_task.cancel()
            try:
                await scanner_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="SkyNetControl", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.settings = settings

    # TrustedHost: reject requests whose Host header doesn't match the
    # configured app_base_url. Defense in depth against a misconfigured
    # proxy passing arbitrary Host headers (the OAuth flow already uses
    # app_settings.app_base_url for redirects, but future code that
    # consults request.url.hostname benefits). Only enforced when the
    # operator has configured a non-localhost app_base_url — the default
    # localhost / dev / test setups send a variety of Host headers
    # (httpx ASGI client → "test", uvicorn dev → "127.0.0.1", etc.) and
    # locking those down would just make every test fixture configure it.
    parsed_base = urlparse(settings.app_base_url)
    base_host = parsed_base.hostname or ""
    if base_host and base_host not in ("localhost", "127.0.0.1"):
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=sorted({base_host, "localhost", "127.0.0.1"}),
        )

    # Security response headers. HSTS only emitted on HTTPS (no point — and
    # actively harmful — on plain HTTP development setups). CSP is permissive
    # by default: blocks third-party script/frame embedding without breaking
    # the SPA bundle that lives same-origin under /assets.
    is_https = settings.app_base_url.startswith("https://")

    # script-src: hash the inline scripts in served index.html so we can
    # drop 'unsafe-inline' (which would otherwise neuter CSP's headline
    # value against XSS sinks). style-src keeps 'unsafe-inline' because
    # React's `style={{...}}` produces inline style attributes on every
    # render and hashing every possible style isn't practical.
    inline_script_hashes = _csp_script_hashes(settings.static_dir)
    script_src = "'self' " + " ".join(inline_script_hashes) if inline_script_hashes else "'self'"
    csp = (
        "default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            response.headers.setdefault("Content-Security-Policy", csp)
            if is_https:
                response.headers.setdefault(
                    "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                )
            return response

    app.add_middleware(_SecurityHeadersMiddleware)

    @app.get("/api/health")
    async def health():
        db_status = "disconnected"
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
                db_status = "connected"
        except Exception:
            pass
        return {"status": "ok", "version": "0.1.0", "database": db_status}

    # Register API routers
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(pat_router, prefix="/api/auth/tokens")
    app.include_router(config_router, prefix="/api/config")
    app.include_router(schedule_router, prefix="/api/schedule")
    app.include_router(activities_router, prefix="/api/activities")
    app.include_router(checkins_router, prefix="/api/checkins")
    app.include_router(reminders_router, prefix="/api/reminders")
    app.include_router(roster_router, prefix="/api/roster")
    app.include_router(notifications_router, prefix="/api/notifications")
    app.include_router(audit_router, prefix="/api/audit")
    app.include_router(delivery_router, prefix="/api/delivery")
    app.include_router(scanner_router, prefix="/api/scanner")
    app.include_router(privacy_router, prefix="/api/privacy")
    app.include_router(oauth_router, prefix="/api/admin")
    app.include_router(smtp_router, prefix="/api/admin")
    app.include_router(test_router, prefix="/api/admin")
    app.include_router(setup_router, prefix="/api/setup")
    app.include_router(recovery_router, prefix="/api")

    # Serve frontend static files if the directory exists. index.html must
    # revalidate every load (it points at content-hashed asset filenames that
    # change every build); the hashed files under /assets/ are safe to cache
    # forever. Nix-store files have a 1970 mtime, so without explicit
    # Cache-Control headers browsers apply heuristic freshness measured in
    # years and hold onto stale HTML across redeploys.
    if os.path.isdir(settings.static_dir):
        assets_dir = os.path.join(settings.static_dir, "assets")
        if os.path.isdir(assets_dir):

            class _ImmutableAssets(StaticFiles):
                async def get_response(self, path, scope):
                    response = await super().get_response(path, scope)
                    if response.status_code == 200:
                        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                    return response

            app.mount("/assets", _ImmutableAssets(directory=assets_dir), name="assets")

        _NO_CACHE = {"Cache-Control": "no-cache"}
        _STATIC_ROOT = os.path.realpath(settings.static_dir)
        _INDEX_HTML = os.path.join(_STATIC_ROOT, "index.html")

        @app.get("/{path:path}")
        async def serve_frontend(path: str):
            # Path traversal guard. Starlette collapses literal "../" segments
            # before routing, but URL-encoded "%2e%2e" reaches here as raw
            # "../" inside `path` — without this check, GET /%2e%2e/etc/passwd
            # would resolve outside _STATIC_ROOT and serve arbitrary files
            # readable by the uvicorn user.
            resolved = os.path.realpath(os.path.join(_STATIC_ROOT, path))
            inside = resolved == _STATIC_ROOT or resolved.startswith(_STATIC_ROOT + os.sep)
            if path and inside and os.path.isfile(resolved):
                return FileResponse(resolved, headers=_NO_CACHE)
            return FileResponse(_INDEX_HTML, headers=_NO_CACHE)

    return app
