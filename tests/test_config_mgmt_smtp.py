import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.smtp import (
    SmtpConfig,
    clear_smtp_config,
    get_smtp_config,
    upsert_smtp_config,
)
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_get_returns_none_when_nothing_configured(db: Session):
    assert get_smtp_config(db) is None


def test_get_returns_none_when_only_partial_rows_exist(db: Session):
    # Host missing → treat as not configured.
    db.add(AppConfig(key="smtp.port", value="587"))
    db.commit()
    assert get_smtp_config(db) is None


def test_upsert_and_get_roundtrip(db: Session):
    cfg = SmtpConfig(
        host="smtp.example.org",
        port=587,
        username="user",
        password="pass",
        from_address="net@example.org",
        use_tls=True,
    )
    upsert_smtp_config(db, cfg)
    got = get_smtp_config(db)
    assert got == cfg


def test_upsert_overwrites_existing(db: Session):
    upsert_smtp_config(
        db,
        SmtpConfig(host="old.example.org", port=25, username="u", password="p",
                   from_address="a@b", use_tls=False),
    )
    upsert_smtp_config(
        db,
        SmtpConfig(host="new.example.org", port=587, username="u2", password="p2",
                   from_address="c@d", use_tls=True),
    )
    got = get_smtp_config(db)
    assert got is not None
    assert got.host == "new.example.org"
    assert got.port == 587
    assert got.use_tls is True


def test_clear_removes_all_smtp_rows(db: Session):
    upsert_smtp_config(
        db,
        SmtpConfig(host="smtp.example.org", port=587, username="u", password="p",
                   from_address="a@b", use_tls=True),
    )
    clear_smtp_config(db)
    assert get_smtp_config(db) is None
    # And no orphaned rows left:
    leftover = db.query(AppConfig).filter(AppConfig.key.like("smtp.%")).all()
    assert leftover == []


def test_port_parses_as_int(db: Session):
    # Tolerate string-stored ports — value column is Text.
    db.add(AppConfig(key="smtp.host", value="smtp.example.org"))
    db.add(AppConfig(key="smtp.port", value="2525"))
    db.add(AppConfig(key="smtp.username", value=""))
    db.add(AppConfig(key="smtp.password", value=""))
    db.add(AppConfig(key="smtp.from_address", value=""))
    db.add(AppConfig(key="smtp.use_tls", value="false"))
    db.commit()
    got = get_smtp_config(db)
    assert got is not None
    assert got.port == 2525
    assert isinstance(got.port, int)


def test_use_tls_parses_truthy_strings(db: Session):
    db.add(AppConfig(key="smtp.host", value="smtp.example.org"))
    db.add(AppConfig(key="smtp.port", value="587"))
    db.add(AppConfig(key="smtp.username", value=""))
    db.add(AppConfig(key="smtp.password", value=""))
    db.add(AppConfig(key="smtp.from_address", value=""))
    db.add(AppConfig(key="smtp.use_tls", value="true"))
    db.commit()
    got = get_smtp_config(db)
    assert got is not None and got.use_tls is True


def test_get_returns_none_when_port_missing(db: Session):
    db.add(AppConfig(key="smtp.host", value="smtp.example.org"))
    # No smtp.port row.
    db.commit()
    assert get_smtp_config(db) is None


def test_get_returns_none_when_port_unparseable(db: Session):
    db.add(AppConfig(key="smtp.host", value="smtp.example.org"))
    db.add(AppConfig(key="smtp.port", value="not-a-number"))
    db.add(AppConfig(key="smtp.username", value=""))
    db.add(AppConfig(key="smtp.password", value=""))
    db.add(AppConfig(key="smtp.from_address", value=""))
    db.add(AppConfig(key="smtp.use_tls", value="false"))
    db.commit()
    assert get_smtp_config(db) is None
