"""add raw_message source_path

Revision ID: ccea830a3f40
Revises: 620025869fb3
Create Date: 2026-06-20 10:50:57.768269

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ccea830a3f40'
down_revision: Union[str, None] = '620025869fb3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("raw_messages") as batch_op:
        batch_op.add_column(sa.Column("source_path", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("raw_messages") as batch_op:
        batch_op.drop_column("source_path")
