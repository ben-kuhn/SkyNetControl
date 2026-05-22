import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.service import get_checkin_modes


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


def test_get_checkin_modes_default(db):
    """Returns default modes when no config is set."""
    modes = get_checkin_modes(db)
    assert "Voice" in modes
    assert "Winlink" in modes
    assert "VARA FM" in modes
    assert len(modes) == 12


def test_get_checkin_modes_custom(db):
    """Returns custom modes when config is set."""
    custom = ["Voice", "Winlink", "Custom Mode"]
    config = AppConfig(key="checkins.modes", value=json.dumps(custom))
    db.add(config)
    db.commit()

    modes = get_checkin_modes(db)
    assert modes == custom


def test_get_checkin_modes_invalid_json(db):
    """Returns defaults on invalid JSON."""
    config = AppConfig(key="checkins.modes", value="not json")
    db.add(config)
    db.commit()

    modes = get_checkin_modes(db)
    assert len(modes) == 12
