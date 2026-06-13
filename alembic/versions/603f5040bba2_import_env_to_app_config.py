"""import env to app_config

Revision ID: 603f5040bba2
Revises: c19a3437d660
Create Date: 2026-06-12 22:26:15.257283

"""
import os
from typing import Sequence, Union

from alembic import op
from sqlalchemy.orm import Session


# revision identifiers, used by Alembic.
revision: str = '603f5040bba2'
down_revision: Union[str, None] = 'c19a3437d660'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from backend.config_mgmt.env_import import import_env_to_app_config

    bind = op.get_bind()
    # `join_transaction_mode="create_savepoint"` makes commits inside the
    # Phase 1 accessor helpers (upsert_*, mark_setup_completed) land on
    # SAVEPOINTs rather than punching through Alembic's outer transaction.
    # On SQLite this is a no-op; on PostgreSQL it preserves the atomicity
    # of the migration + alembic_version stamp.
    session = Session(bind=bind, join_transaction_mode="create_savepoint")
    try:
        import_env_to_app_config(session, dict(os.environ))
        session.commit()
    finally:
        session.close()


def downgrade() -> None:
    # No downgrade — env vars persist in the environment, so the rows can be
    # re-imported by re-running the upgrade. Removing oauth.* / smtp.* rows
    # here would discard wizard / Config-page edits made after the upgrade.
    pass
