import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings

    if settings.jwt_secret_key == _DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "SKYNET_JWT_SECRET_KEY is still the default placeholder value. "
            "Generate one with `openssl rand -hex 32` and set it via the "
            "SKYNET_JWT_SECRET_KEY env var before starting the server."
        )

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

        @app.get("/{path:path}")
        async def serve_frontend(path: str):
            file_path = os.path.join(settings.static_dir, path)
            if path and os.path.isfile(file_path):
                return FileResponse(file_path, headers=_NO_CACHE)
            return FileResponse(os.path.join(settings.static_dir, "index.html"), headers=_NO_CACHE)

    return app
