"""create user titles

Revision ID: fe8c5592755b
Revises: b2ea22af8268
Create Date: 2026-11-06 20:15:15.379548
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fe8c5592755b"
down_revision: str | Sequence[str] | None = "b2ea22af8268"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_titles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("title_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "watching",
                "planned",
                "completed",
                "dropped",
                name="list_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 10", name=op.f("ck_user_titles_rating_range")),
        sa.ForeignKeyConstraint(
            ["title_id"],
            ["titles.id"],
            name=op.f("fk_user_titles_title_id_titles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_titles_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_titles")),
        sa.UniqueConstraint("user_id", "title_id", name=op.f("uq_user_titles_user_id_title_id")),
    )
    op.create_index(op.f("ix_user_titles_title_id"), "user_titles", ["title_id"], unique=False)
    op.create_index(
        "ix_user_titles_user_id_status", "user_titles", ["user_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_user_titles_user_id_status", table_name="user_titles")
    op.drop_index(op.f("ix_user_titles_title_id"), table_name="user_titles")
    op.drop_table("user_titles")
