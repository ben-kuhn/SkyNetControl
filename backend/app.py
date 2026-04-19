import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.config import Settings, settings as default_settings
from backend.db.session import create_engine_from_url, create_session_factory
from backend.auth.routes import auth_router
from backend.config_mgmt.routes import config_router
from backend.modules.schedule.routes import schedule_router
from backend.modules.activities.routes import activities_router
from backend.modules.checkins.routes import checkins_router
from backend.modules.reminders.routes import reminders_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings
    app = FastAPI(title="SkyNetControl", version="0.1.0")

    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)
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
    app.include_router(config_router, prefix="/api/config")
    app.include_router(schedule_router, prefix="/api/schedule")
    app.include_router(activities_router, prefix="/api/activities")
    app.include_router(checkins_router, prefix="/api/checkins")
    app.include_router(reminders_router, prefix="/api/reminders")

    # Serve frontend static files if the directory exists
    if os.path.isdir(settings.static_dir):
        assets_dir = os.path.join(settings.static_dir, "assets")
        if os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{path:path}")
        async def serve_frontend(path: str):
            file_path = os.path.join(settings.static_dir, path)
            if path and os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(os.path.join(settings.static_dir, "index.html"))

    return app
