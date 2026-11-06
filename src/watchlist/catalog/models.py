from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Enum,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from watchlist.db import Base


class TitleType(enum.StrEnum):
    SERIES = "series"
    MOVIE = "movie"

    @property
    def tmdb_type(self) -> str:
        return "tv" if self is TitleType.SERIES else "movie"

    @classmethod
    def from_tmdb(cls, value: str) -> TitleType:
        return cls.SERIES if value == "tv" else cls.MOVIE


class Title(Base):
    __tablename__ = "titles"
    __table_args__ = (UniqueConstraint("tmdb_id", "type"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer)
    type: Mapped[TitleType] = mapped_column(
        Enum(
            TitleType,
            name="title_type",
            native_enum=False,
            create_constraint=True,
            length=10,
            values_callable=lambda items: [item.value for item in items],
        )
    )
    name: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str | None] = mapped_column(String(500))
    overview: Mapped[str] = mapped_column(Text, default="")
    poster_path: Mapped[str | None] = mapped_column(String(255))
    release_date: Mapped[date | None]
    status: Mapped[str | None] = mapped_column(String(50))
    genres: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    last_synced_at: Mapped[datetime | None]

    seasons: Mapped[list[Season]] = relationship(
        back_populates="title",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Season.number",
    )

    @property
    def is_series(self) -> bool:
        return self.type is TitleType.SERIES


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("title_id", "number"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    title_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("titles.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(500), default="")
    air_date: Mapped[date | None]

    title: Mapped[Title] = relationship(back_populates="seasons")
    episodes: Mapped[list[Episode]] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Episode.number",
    )


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("season_id", "number"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    season_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("seasons.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(500), default="")
    air_date: Mapped[date | None] = mapped_column(index=True)
    runtime: Mapped[int | None] = mapped_column(Integer)

    season: Mapped[Season] = relationship(back_populates="episodes")
