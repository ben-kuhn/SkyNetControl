import pytest
from sqlalchemy import text

from backend.db.session import create_engine_from_url, create_session_factory


@pytest.mark.asyncio
async def test_sqlite_engine_connects():
    engine = create_engine_from_url("sqlite:///")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_session_factory_creates_session():
    engine = create_engine_from_url("sqlite:///")
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        result = session.execute(text("SELECT 1"))
        assert result.scalar() == 1
    engine.dispose()
