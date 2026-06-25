import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.modules.nets.models import Net, NetMembership, NetConfig, NetRole
from backend.auth.models import User  # ensure user table exists


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_net_round_trip():
    db = _session()
    net = Net(slug="w0ne", name="W0NE Net")
    db.add(net)
    db.commit()
    assert net.id is not None
    assert net.is_public is True


def test_net_membership_pk_is_composite():
    db = _session()
    net = Net(slug="x", name="X")
    db.add(net)
    user = User(callsign="W0XYZ", oidc_subject="s1", name="Test")
    db.add(user)
    db.commit()

    m = NetMembership(user_callsign="W0XYZ", net_id=net.id, role=NetRole.VIEWER)
    db.add(m)
    db.commit()
    assert (db.query(NetMembership).count()) == 1


def test_net_config_round_trip():
    db = _session()
    net = Net(slug="x", name="X")
    db.add(net)
    db.commit()
    db.add(NetConfig(net_id=net.id, key="foo", value="bar"))
    db.commit()
    got = db.query(NetConfig).one()
    assert got.value == "bar"
