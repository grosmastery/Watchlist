"""create catalog tables

Revision ID: b2ea22af8268
Revises: 159a92c005dc
Create Date: 2026-11-06 00:30:48.493178
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2ea22af8268"
down_revision: str | Sequence[str] | None = "159a92c005dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "titles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "series",
                "movie",
                name="title_type",
                native_enum=False,
                create_constraint=True,
                length=10,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("original_name", sa.String(length=500), nullable=True),
        sa.Column("overview", sa.Text(), nullable=False),
        sa.Column("poster_path", sa.String(length=255), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("genres", postgresql.ARRAY(sa.String(length=100)), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_titles")),
        sa.UniqueConstraint("tmdb_id", "type", name=op.f("uq_titles_tmdb_id_type")),
    )
    op.create_table(
        "seasons",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("title_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("air_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["title_id"], ["titles.id"], name=op.f("fk_seasons_title_id_titles"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seasons")),
        sa.UniqueConstraint("title_id", "number", name=op.f("uq_seasons_title_id_number")),
    )
    op.create_table(
        "episodes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("season_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("air_date", sa.Date(), nullable=True),
        sa.Column("runtime", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.id"],
            name=op.f("fk_episodes_season_id_seasons"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_episodes")),
        sa.UniqueConstraint("season_id", "number", name=op.f("uq_episodes_season_id_number")),
    )
    op.create_index(op.f("ix_episodes_air_date"), "episodes", ["air_date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_episodes_air_date"), table_name="episodes")
    op.drop_table("episodes")
    op.drop_table("seasons")
    op.drop_table("titles")
