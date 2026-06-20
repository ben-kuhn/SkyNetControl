"""Verify the seed-template constants exposed by /template-defaults match
what the migrations actually leave in the DB on a fresh install.

If a migration changes the seeded body but the constant doesn't (or
vice versa), this test fails — preventing the "+ New template" UI from
silently pre-filling with stale text.
"""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from backend.modules.reminders.seeds import SEED_REMINDER_TEMPLATES
from backend.modules.roster.seeds import SEED_ROSTER_TEMPLATES

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def upgraded_db(tmp_path, monkeypatch):
    """Run all alembic migrations against a fresh sqlite file.

    File-based (not :memory:) so alembic's separate connection sees the
    same DB. env.py overrides sqlalchemy.url with settings.database_url,
    so we mutate the live settings singleton instead of just passing the
    URL into the Config.
    """
    db_path = tmp_path / "seed_check.db"
    url = f"sqlite:///{db_path}"

    from backend import config as backend_config

    monkeypatch.setattr(backend_config.settings, "database_url", url)

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_reminder_seeds_match_migrations(upgraded_db):
    """For each seed constant, the corresponding migrated row matches."""
    from backend.modules.reminders.models import TemplateType

    with upgraded_db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name, template_type, subject_template, body_template, lead_time_days "
                "FROM reminder_templates WHERE is_default = 1 ORDER BY name"
            )
        ).fetchall()

    by_name = {r[0]: r for r in rows}
    for seed in SEED_REMINDER_TEMPLATES:
        row = by_name.get(seed["name"])
        assert row is not None, f"seed {seed['name']!r} not found in DB after migrations"
        # template_type is stored as the enum NAME ('REGULAR_CHECKIN') in
        # the DB; the API/constants use the enum VALUE ('regular_checkin').
        # See migration c19a3437d660.
        assert TemplateType[row[1]].value == seed["template_type"]
        assert row[2] == seed["subject_template"]
        assert row[3] == seed["body_template"]
        assert row[4] == seed["lead_time_days"]


def test_roster_seeds_match_migrations(upgraded_db):
    with upgraded_db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name, subject_template, header_template, welcome_template, "
                "comments_template, footer_template, lead_time_days "
                "FROM roster_templates WHERE is_default = 1 ORDER BY name"
            )
        ).fetchall()

    by_name = {r[0]: r for r in rows}
    for seed in SEED_ROSTER_TEMPLATES:
        row = by_name.get(seed["name"])
        assert row is not None, f"seed {seed['name']!r} not found in DB after migrations"
        assert row[1] == seed["subject_template"]
        assert row[2] == seed["header_template"]
        assert row[3] == seed["welcome_template"]
        assert row[4] == seed["comments_template"]
        assert row[5] == seed["footer_template"]
        assert row[6] == seed["lead_time_days"]
