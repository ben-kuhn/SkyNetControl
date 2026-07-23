"""Tests for Fix 2: net-scoped event PAT connect route removed."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.modules.events.pat_routes import pat_router


@pytest.fixture
def pat_app():
    app = FastAPI()
    app.include_router(pat_router)
    return app


@pytest.mark.asyncio
async def test_net_scoped_event_pat_connect_is_removed(pat_app):
    """POST /api/nets/{slug}/events/{id}/pat/connect must be 404 (route removed)."""
    async with AsyncClient(transport=ASGITransport(app=pat_app), base_url="http://test") as c:
        r = await c.post("/api/nets/testnet/events/42/pat/connect", json={})
    assert r.status_code == 404, f"Expected 404 (route removed), got {r.status_code}"


def test_event_scoped_pat_connect_route_is_registered():
    """The event-scoped /api/events/{id}/pat/connect route must be registered
    in event_pat_router (check that the path exists in the router's routes)."""
    from backend.modules.events.pat_routes import event_pat_router

    routes = [(r.path, r.methods) for r in event_pat_router.routes]
    # Expect the event-scoped connect route to be present
    assert any(
        "/api/events/{event_id}/pat/connect" in path and "POST" in (methods or set())
        for path, methods in routes
    ), f"Event PAT connect route not found in registered routes: {routes}"
