from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from watchlist.catalog.models import Title
from watchlist.db import Base

RATING_MIN = 1
RATING_MAX = 10


class ListStatus(enum.StrEnum):
    WATCHING = "watching"
    PLANNED = "planned"
    COMPLETED = "completed"
    DROPPED = "dropped"

    @property
    def label(self) -> str:
        return STATUS_LABELS[self]


STATUS_LABELS = {
    ListStatus.WATCHING: "Дивлюсь",
    ListStatus.PLANNED: "Хочу подивитись",
    ListStatus.COMPLETED: "Переглянуто",
    ListStatus.DROPPED: "Закинув",
}


class UserTitle(Base):
    __tablename__ = "user_titles"
    __table_args__ = (
        UniqueConstraint("user_id", "title_id"),
        CheckConstraint(f"rating BETWEEN {RATING_MIN} AND {RATING_MAX}", name="rating_range"),
        Index("ix_user_titles_user_id_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    title_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("titles.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[ListStatus] = mapped_column(
        Enum(
            ListStatus,
            name="list_status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda items: [item.value for item in items],
        )
    )
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    title: Mapped[Title] = relationship()
