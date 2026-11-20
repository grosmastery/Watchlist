"""add note to user titles

Revision ID: 5b2e8d71c9a4
Revises: a7c4e19b3f52
Create Date: 2026-11-20 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b2e8d71c9a4"
down_revision: str | Sequence[str] | None = "a7c4e19b3f52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_titles", sa.Column("note", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_user_titles_note_length"), "user_titles", "char_length(note) <= 2000"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_user_titles_note_length"), "user_titles", type_="check")
    op.drop_column("user_titles", "note")
