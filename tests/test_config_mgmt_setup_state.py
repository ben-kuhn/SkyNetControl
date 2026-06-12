import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.setup_state import is_setup_completed, mark_setup_completed
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_unset_is_not_completed(db: Session):
    assert is_setup_completed(db) is False


def test_mark_then_check(db: Session):
    mark_setup_completed(db)
    assert is_setup_completed(db) is True


def test_mark_is_idempotent(db: Session):
    mark_setup_completed(db)
    mark_setup_completed(db)
    assert is_setup_completed(db) is True
    rows = db.query(AppConfig).filter(AppConfig.key == "setup_completed").all()
    assert len(rows) == 1


def test_only_truthy_string_counts_as_completed(db: Session):
    # Pre-Phase-2 deployments may have populated the row by hand; treat
    # presence with a non-truthy value as "not completed" so we don't
    # accidentally short-circuit a wizard that hasn't really finished.
    db.add(AppConfig(key="setup_completed", value="false"))
    db.commit()
    assert is_setup_completed(db) is False
