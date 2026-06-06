"""Tests for the cross-engine database copy helper."""
import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from backend.auth.models import User, UserRole
from backend.cli.db_copy import copy_database
from backend.config_mgmt.models import AppConfig
from backend.db.base import Base
from backend.modules.schedule.models import NetSeason
from datetime import date


def _make_db(path: str) -> str:
    """Create an empty migrated DB at the given path. Returns sqlite URL."""
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


def test_copy_database_round_trips_rows(tmp_path):
    src_url = _make_db(str(tmp_path / "src.db"))
    dst_url = _make_db(str(tmp_path / "dst.db"))

    src_engine = create_engine(src_url)
    SrcSession = sessionmaker(bind=src_engine)
    with SrcSession() as s:
        s.add(User(callsign="K0XYZ", oidc_subject="g:x", name="Alice", role=UserRole.ADMIN))
        s.add(AppConfig(key="default_net_control", value="K0XYZ"))
        s.add(NetSeason(name="Spring", start_date=date(2026, 4, 1), end_date=date(2026, 6, 30), day_of_week=3))
        s.commit()
    src_engine.dispose()

    copy_database(src_url, dst_url)

    dst_engine = create_engine(dst_url)
    DstSession = sessionmaker(bind=dst_engine)
    with DstSession() as s:
        users = s.execute(select(User)).scalars().all()
        assert len(users) == 1
        assert users[0].callsign == "K0XYZ"
        assert users[0].role == UserRole.ADMIN

        configs = s.execute(select(AppConfig)).scalars().all()
        assert {c.key: c.value for c in configs} == {"default_net_control": "K0XYZ"}

        seasons = s.execute(select(NetSeason)).scalars().all()
        assert len(seasons) == 1
        assert seasons[0].name == "Spring"
    dst_engine.dispose()


def test_copy_database_refuses_when_target_unmigrated(tmp_path):
    src_url = _make_db(str(tmp_path / "src.db"))
    # Target DB exists but has NO schema (no Base.metadata.create_all)
    dst_path = tmp_path / "dst.db"
    dst_path.touch()
    dst_url = f"sqlite:///{dst_path}"

    with pytest.raises(RuntimeError, match="target.*missing.*tables"):
        copy_database(src_url, dst_url)


def test_copy_database_refuses_when_target_has_data(tmp_path):
    src_url = _make_db(str(tmp_path / "src.db"))
    dst_url = _make_db(str(tmp_path / "dst.db"))

    dst_engine = create_engine(dst_url)
    DstSession = sessionmaker(bind=dst_engine)
    with DstSession() as s:
        s.add(User(callsign="W0EXISTING", oidc_subject="g:e", name="Existing", role=UserRole.ADMIN))
        s.commit()
    dst_engine.dispose()

    with pytest.raises(RuntimeError, match="target.*not empty"):
        copy_database(src_url, dst_url)


def test_copy_database_replace_truncates_target_first(tmp_path):
    """In real deployments the freshly-migrated target always has seed rows
    (default templates etc.), so --replace must wipe them before copying."""
    src_url = _make_db(str(tmp_path / "src.db"))
    dst_url = _make_db(str(tmp_path / "dst.db"))

    src_engine = create_engine(src_url)
    SrcSession = sessionmaker(bind=src_engine)
    with SrcSession() as s:
        s.add(User(callsign="K0XYZ", oidc_subject="g:x", name="Alice", role=UserRole.ADMIN))
        s.commit()
    src_engine.dispose()

    # Pre-populate target with conflicting data (simulating seed rows from migrations).
    dst_engine = create_engine(dst_url)
    DstSession = sessionmaker(bind=dst_engine)
    with DstSession() as s:
        s.add(User(callsign="W0PRESEED", oidc_subject="g:p", name="Preseed", role=UserRole.VIEWER))
        s.commit()
    dst_engine.dispose()

    copy_database(src_url, dst_url, replace=True)

    dst_engine = create_engine(dst_url)
    with sessionmaker(bind=dst_engine)() as s:
        users = s.execute(select(User)).scalars().all()
        assert len(users) == 1
        assert users[0].callsign == "K0XYZ"
    dst_engine.dispose()
