from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.db.base import Base
# Import all models so Base.metadata includes their tables
import backend.auth.models  # noqa: F401
import backend.config_mgmt.models  # noqa: F401
import backend.modules.schedule.models  # noqa: F401
import backend.modules.activities.models  # noqa: F401
import backend.modules.checkins.models  # noqa: F401
import backend.modules.reminders.models  # noqa: F401
import backend.modules.roster.models  # noqa: F401
from backend.modules.notifications import models as notifications_models  # noqa: F401
import backend.auth.pat_models  # noqa: F401
import backend.audit.models  # noqa: F401
import backend.integrations.delivery.models  # noqa: F401
import backend.integrations.callbook.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
