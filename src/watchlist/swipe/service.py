from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.catalog.models import Title
from watchlist.home.recommendations import TitleKey
from watchlist.integrations.tmdb_schemas import MediaType
from watchlist.swipe.models import Swipe
from watchlist.tracking.models import UserTitle


class SwipeDecision(BaseModel):
    media_type: Literal["tv", "movie"]
    tmdb_id: int = Field(gt=0)
    liked: bool
    name: str = Field(min_length=1, max_length=500)
    poster_path: str | None = Field(default=None, max_length=255, pattern=r"^/[\w.\-]+$")
    subtitle: str = Field(max_length=100)


async def excluded_keys(session: AsyncSession, user_id: int) -> set[TitleKey]:
    swiped = await session.execute(
        select(Swipe.media_type, Swipe.tmdb_id).where(Swipe.user_id == user_id)
    )
    tracked = await session.execute(
        select(Title.type, Title.tmdb_id)
        .join(UserTitle, UserTitle.title_id == Title.id)
        .where(UserTitle.user_id == user_id)
    )
    keys: set[TitleKey] = {(kind.tmdb_type, tmdb_id) for kind, tmdb_id in tracked}
    for media_type, tmdb_id in swiped:
        kind: MediaType = "tv" if media_type == "tv" else "movie"
        keys.add((kind, tmdb_id))
    return keys


async def record_swipe(session: AsyncSession, user_id: int, decision: SwipeDecision) -> None:
    values = decision.model_dump()
    statement = insert(Swipe).values(user_id=user_id, **values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[Swipe.user_id, Swipe.media_type, Swipe.tmdb_id],
            set_={**values, "swiped_at": func.now()},
        )
    )


async def liked_count(session: AsyncSession, user_id: int) -> int:
    count = await session.scalar(
        select(func.count()).select_from(Swipe).where(Swipe.user_id == user_id, Swipe.liked)
    )
    return count or 0


async def list_liked(session: AsyncSession, user_id: int) -> list[Swipe]:
    rows = await session.scalars(
        select(Swipe)
        .where(Swipe.user_id == user_id, Swipe.liked)
        .order_by(Swipe.swiped_at.desc(), Swipe.tmdb_id.desc())
    )
    return list(rows)


async def remove_liked(
    session: AsyncSession, user_id: int, media_type: MediaType, tmdb_id: int
) -> None:
    await session.execute(
        delete(Swipe).where(
            Swipe.user_id == user_id,
            Swipe.media_type == media_type,
            Swipe.tmdb_id == tmdb_id,
            Swipe.liked,
        )
    )
