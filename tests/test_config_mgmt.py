import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.service import get_config_value, set_config_value, get_all_config


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_set_and_get_config(db: Session):
    set_config_value(db, "net_address", "w0ne@winlink.org")
    value = get_config_value(db, "net_address")
    assert value == "w0ne@winlink.org"


def test_get_nonexistent_config(db: Session):
    value = get_config_value(db, "nonexistent")
    assert value is None


def test_get_config_with_default(db: Session):
    value = get_config_value(db, "nonexistent", default="fallback")
    assert value == "fallback"


def test_update_existing_config(db: Session):
    set_config_value(db, "net_address", "old@winlink.org")
    set_config_value(db, "net_address", "new@winlink.org")
    value = get_config_value(db, "net_address")
    assert value == "new@winlink.org"


def test_get_all_config(db: Session):
    set_config_value(db, "net_address", "w0ne@winlink.org")
    set_config_value(db, "default_net_control", "W0NE")
    all_config = get_all_config(db)
    assert all_config == {
        "net_address": "w0ne@winlink.org",
        "default_net_control": "W0NE",
    }
