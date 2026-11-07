"""create watched episodes

Revision ID: d21c30112126
Revises: fe8c5592755b
Create Date: 2026-11-07 22:13:40.748734
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d21c30112126"
down_revision: str | Sequence[str] | None = "fe8c5592755b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watched_episodes",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("episode_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "watched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            ["episodes.id"],
            name=op.f("fk_watched_episodes_episode_id_episodes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_watched_episodes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "episode_id", name=op.f("pk_watched_episodes")),
    )
    op.create_index(
        op.f("ix_watched_episodes_episode_id"), "watched_episodes", ["episode_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_watched_episodes_episode_id"), table_name="watched_episodes")
    op.drop_table("watched_episodes")
