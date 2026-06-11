import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
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


# When the DB has no value for a key, fall back to SKYNET_<KEY> from the
# environment. This is what the deployment docs promise: setting e.g.
# `services.skynetcontrol.settings.PAT_MAILBOX_PATH` should let the app
# read it without needing an admin to touch the Config page.
def test_env_fallback_when_db_unset(db: Session, monkeypatch):
    monkeypatch.setenv("SKYNET_PAT_MAILBOX_PATH", "/from/env")
    assert get_config_value(db, "pat_mailbox_path") == "/from/env"


def test_db_value_wins_over_env(db: Session, monkeypatch):
    monkeypatch.setenv("SKYNET_PAT_MAILBOX_PATH", "/from/env")
    set_config_value(db, "pat_mailbox_path", "/from/db")
    assert get_config_value(db, "pat_mailbox_path") == "/from/db"


def test_env_fallback_dotted_key(db: Session, monkeypatch):
    # Dotted keys (e.g. callbook.qrz.username) map to SKYNET_<UPPER>__<UPPER>__…
    # following the Pydantic-settings nested-env-delimiter convention.
    monkeypatch.setenv("SKYNET_CALLBOOK__QRZ__USERNAME", "envuser")
    assert get_config_value(db, "callbook.qrz.username") == "envuser"


def test_default_returned_when_neither_db_nor_env(db: Session, monkeypatch):
    monkeypatch.delenv("SKYNET_PAT_MAILBOX_PATH", raising=False)
    assert get_config_value(db, "pat_mailbox_path", default="fallback") == "fallback"
