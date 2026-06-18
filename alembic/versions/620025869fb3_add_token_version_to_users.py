"""add token_version to users

Backs JWT invalidation on logout / role-change / delete: each access
token carries the user row's current `token_version` claim, and the
auth dependency rejects any token whose claim doesn't match. Logout,
role mutation, and account deletion bump the column, immediately
invalidating every outstanding JWT for that user.

Revision ID: 620025869fb3
Revises: e837ac271887
"""
import sqlalchemy as sa
from alembic import op

revision = "620025869fb3"
down_revision = "e837ac271887"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
