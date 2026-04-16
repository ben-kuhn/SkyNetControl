import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", debug=True)


@pytest.fixture
def app(test_settings):
    return create_app(settings=test_settings)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
