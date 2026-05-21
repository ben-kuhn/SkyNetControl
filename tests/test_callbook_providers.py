from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base


def test_callbook_cache_model():
    """CallbookCache model can be created and queried."""
    from backend.integrations.callbook.models import CallbookCache

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as db:
        entry = CallbookCache(
            callsign="W0ABC",
            name="John Smith",
            city="Denver",
            county="Denver",
            state="CO",
            country="United States",
            latitude=39.7392,
            longitude=-104.9903,
            source="hamqth",
            fetched_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        db.add(entry)
        db.commit()

        result = db.get(CallbookCache, "W0ABC")
        assert result is not None
        assert result.name == "John Smith"
        assert result.source == "hamqth"
        assert result.latitude == 39.7392
