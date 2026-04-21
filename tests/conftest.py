import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from backend.config import Settings
from backend.db.base import Base


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", debug=True)


@pytest.fixture
def app(test_settings):
    application = create_app(settings=test_settings)
    # Create all tables for tests
    Base.metadata.create_all(application.state.engine)
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
