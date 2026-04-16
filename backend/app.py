from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="SkyNetControl", version="0.1.0")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    return app
