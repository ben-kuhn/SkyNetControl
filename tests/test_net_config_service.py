"""Tests for backend.modules.nets.config_service."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.nets.models import Net, NetConfig
from backend.modules.nets.config_service import get_net_config, set_net_config


def _make_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory()


def _make_net(db, slug="cfg-net"):
    net = Net(slug=slug, name="Config Net")
    db.add(net)
    db.commit()
    return net


# ---------------------------------------------------------------------------
# get_net_config
# ---------------------------------------------------------------------------


def test_get_net_config_returns_default_when_missing():
    db = _make_db()
    net = _make_net(db)
    result = get_net_config(db, net.id, "missing.key", default="fallback")
    assert result == "fallback"


def test_get_net_config_returns_none_when_no_default():
    db = _make_db()
    net = _make_net(db)
    result = get_net_config(db, net.id, "missing.key")
    assert result is None


def test_get_net_config_returns_stored_value():
    db = _make_db()
    net = _make_net(db)
    set_net_config(db, net.id, "net.name", "W0NE Weekly")
    result = get_net_config(db, net.id, "net.name")
    assert result == "W0NE Weekly"


# ---------------------------------------------------------------------------
# set_net_config
# ---------------------------------------------------------------------------


def test_set_net_config_creates_row():
    db = _make_db()
    net = _make_net(db)
    set_net_config(db, net.id, "smtp.host", "mail.example.com")
    row = db.get(NetConfig, (net.id, "smtp.host"))
    assert row is not None
    assert row.value == "mail.example.com"


def test_set_net_config_updates_existing_row():
    db = _make_db()
    net = _make_net(db)
    set_net_config(db, net.id, "key1", "first")
    set_net_config(db, net.id, "key1", "second")
    row = db.get(NetConfig, (net.id, "key1"))
    assert row.value == "second"
    # Only one row exists
    count = db.query(NetConfig).filter(NetConfig.net_id == net.id, NetConfig.key == "key1").count()
    assert count == 1


def test_set_net_config_different_nets_isolated():
    db = _make_db()
    net_a = _make_net(db, "net-a")
    net_b = _make_net(db, "net-b")
    set_net_config(db, net_a.id, "shared.key", "value-a")
    set_net_config(db, net_b.id, "shared.key", "value-b")
    assert get_net_config(db, net_a.id, "shared.key") == "value-a"
    assert get_net_config(db, net_b.id, "shared.key") == "value-b"


def test_set_net_config_updates_updated_at():
    """updated_at should be refreshed on update (if column onupdate fires)."""
    db = _make_db()
    net = _make_net(db)
    set_net_config(db, net.id, "ts.key", "v1")
    row1 = db.get(NetConfig, (net.id, "ts.key"))
    t1 = row1.updated_at

    # Force a distinct write
    set_net_config(db, net.id, "ts.key", "v2")
    db.expire(row1)
    row2 = db.get(NetConfig, (net.id, "ts.key"))
    # updated_at is set explicitly in set_net_config on update path
    assert row2.updated_at >= t1


def test_set_net_config_bulk_upserts_all_keys():
    from backend.modules.nets.config_service import set_net_config_bulk

    db = _make_db()
    net = _make_net(db)
    set_net_config_bulk(db, net.id, {"k1": "v1", "k2": "v2"})
    assert get_net_config(db, net.id, "k1") == "v1"
    assert get_net_config(db, net.id, "k2") == "v2"


def test_set_net_config_bulk_updates_existing_keys():
    from backend.modules.nets.config_service import set_net_config_bulk

    db = _make_db()
    net = _make_net(db)
    set_net_config(db, net.id, "k1", "old")
    set_net_config_bulk(db, net.id, {"k1": "new", "k2": "fresh"})
    assert get_net_config(db, net.id, "k1") == "new"
    assert get_net_config(db, net.id, "k2") == "fresh"


def test_set_net_config_bulk_isolated_per_net():
    from backend.modules.nets.config_service import set_net_config_bulk

    db = _make_db()
    net_a = _make_net(db, "net-a")
    net_b = _make_net(db, "net-b")
    set_net_config_bulk(db, net_a.id, {"k": "a"})
    set_net_config_bulk(db, net_b.id, {"k": "b"})
    assert get_net_config(db, net_a.id, "k") == "a"
    assert get_net_config(db, net_b.id, "k") == "b"


def test_set_net_config_bulk_empty_dict_is_noop():
    from backend.modules.nets.config_service import set_net_config_bulk

    db = _make_db()
    net = _make_net(db)
    set_net_config_bulk(db, net.id, {})
    assert get_net_config(db, net.id, "anything") is None
