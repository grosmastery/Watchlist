from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from watchlist.db import Base


class Swipe(Base):
    __tablename__ = "swipes"
    __table_args__ = (
        CheckConstraint("media_type IN ('tv', 'movie')", name="media_type_valid"),
        Index("ix_swipes_user_id_liked_swiped_at", "user_id", "liked", "swiped_at"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    media_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    liked: Mapped[bool] = mapped_column(Boolean)
    name: Mapped[str] = mapped_column(String(500))
    poster_path: Mapped[str | None] = mapped_column(String(255))
    subtitle: Mapped[str] = mapped_column(String(100))
    swiped_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def url(self) -> str:
        return f"/tmdb/{self.media_type}/{self.tmdb_id}"
