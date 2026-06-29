"""seed net_config from app_config and set winlink_enabled

Revision ID: cc6d665bb77e
Revises: 7e2d4f81b3a9
Create Date: 2026-06-29 14:33:39.760664

Moves nine previously-global keys from app_config into per-net net_config
(see docs/superpowers/specs/2026-06-29-settings-reorganization-design.md).
For each net missing a row for a moved key, copies the value from
app_config. Sets winlink_enabled=true for every net (preserves today's
Winlink-net behavior; non-Winlink-net operators opt out in the UI).
Finally deletes the moved keys from app_config.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc6d665bb77e'
down_revision: Union[str, None] = '7e2d4f81b3a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MOVED_KEYS = (
    "default_net_control",
    "net_address",
    "pat_mailbox_path",
    "scanner.enabled",
    "scanner.interval_minutes",
    "delivery.backends",
    "delivery.email.to_address",
    "delivery.groupsio.group_name",
    "delivery.winlink.target_address",
)


def upgrade() -> None:
    conn = op.get_bind()
    # Reflect just the tables we need
    nets = sa.table("nets", sa.column("id", sa.Integer))
    app_config = sa.table(
        "app_config",
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
    )
    net_config = sa.table(
        "net_config",
        sa.column("net_id", sa.Integer),
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
        sa.column("updated_at", sa.DateTime),
    )

    net_ids = [r.id for r in conn.execute(sa.select(nets.c.id))]
    moved_values: dict[str, str] = {}
    for row in conn.execute(
        sa.select(app_config.c.key, app_config.c.value).where(app_config.c.key.in_(MOVED_KEYS))
    ):
        moved_values[row.key] = row.value

    now = sa.func.now()

    for net_id in net_ids:
        existing = {
            r.key
            for r in conn.execute(
                sa.select(net_config.c.key).where(net_config.c.net_id == net_id)
            )
        }
        # Seed each moved key the net doesn't already have
        for key in MOVED_KEYS:
            if key in existing:
                continue
            if key not in moved_values:
                continue
            conn.execute(
                net_config.insert().values(
                    net_id=net_id,
                    key=key,
                    value=moved_values[key],
                    updated_at=now,
                )
            )
        # winlink_enabled: default true unless already present
        if "winlink_enabled" not in existing:
            conn.execute(
                net_config.insert().values(
                    net_id=net_id,
                    key="winlink_enabled",
                    value="true",
                    updated_at=now,
                )
            )

    # Drop the moved keys from app_config
    conn.execute(app_config.delete().where(app_config.c.key.in_(MOVED_KEYS)))


def downgrade() -> None:
    # Data migration — downgrade is intentionally a no-op. Restoring the
    # previous global rows from per-net values is ambiguous on a multi-net
    # install (which net's value wins?). Operators who need to roll back
    # should restore from a backup.
    pass
