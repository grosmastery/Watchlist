"""create swipes

Revision ID: a7c4e19b3f52
Revises: d21c30112126
Create Date: 2026-11-09 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e19b3f52"
down_revision: str | Sequence[str] | None = "d21c30112126"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "swipes",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=10), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("liked", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("poster_path", sa.String(length=255), nullable=True),
        sa.Column("subtitle", sa.String(length=100), nullable=False),
        sa.Column(
            "swiped_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "media_type IN ('tv', 'movie')", name=op.f("ck_swipes_media_type_valid")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_swipes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "media_type", "tmdb_id", name=op.f("pk_swipes")),
    )
    op.create_index(
        "ix_swipes_user_id_liked_swiped_at",
        "swipes",
        ["user_id", "liked", "swiped_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_swipes_user_id_liked_swiped_at", table_name="swipes")
    op.drop_table("swipes")
