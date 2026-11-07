from enum import Enum, auto

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from watchlist.catalog.models import Title
from watchlist.tracking.models import RATING_MAX, RATING_MIN, ListStatus, UserTitle


class Unset(Enum):
    UNSET = auto()


UNSET = Unset.UNSET


class TitleNotFoundError(Exception):
    pass


class EntryNotFoundError(Exception):
    pass


class InvalidRatingError(ValueError):
    pass


def validate_rating(rating: int | None) -> int | None:
    if rating is not None and not RATING_MIN <= rating <= RATING_MAX:
        raise InvalidRatingError(rating)
    return rating


async def get_entry(session: AsyncSession, user_id: int, title_id: int) -> UserTitle | None:
    return await session.scalar(
        select(UserTitle)
        .where(UserTitle.user_id == user_id, UserTitle.title_id == title_id)
        .execution_options(populate_existing=True)
    )


async def add_to_list(
    session: AsyncSession,
    user_id: int,
    title_id: int,
    status: ListStatus = ListStatus.PLANNED,
) -> UserTitle:
    if await session.get(Title, title_id) is None:
        raise TitleNotFoundError(title_id)
    statement = (
        insert(UserTitle)
        .values(user_id=user_id, title_id=title_id, status=status)
        .on_conflict_do_nothing(index_elements=[UserTitle.user_id, UserTitle.title_id])
    )
    await session.execute(statement)
    entry = await get_entry(session, user_id, title_id)
    if entry is None:
        raise EntryNotFoundError(title_id)
    return entry


async def update_entry(
    session: AsyncSession,
    user_id: int,
    title_id: int,
    *,
    status: ListStatus | Unset = UNSET,
    rating: int | Unset | None = UNSET,
) -> UserTitle:
    entry = await get_entry(session, user_id, title_id)
    if entry is None:
        raise EntryNotFoundError(title_id)
    if not isinstance(status, Unset):
        entry.status = status
    if not isinstance(rating, Unset):
        entry.rating = validate_rating(rating)
    await session.flush()
    await session.refresh(entry)
    return entry


async def remove_from_list(session: AsyncSession, user_id: int, title_id: int) -> bool:
    result = await session.execute(
        delete(UserTitle)
        .where(UserTitle.user_id == user_id, UserTitle.title_id == title_id)
        .returning(UserTitle.id)
    )
    return result.first() is not None


async def list_entries(
    session: AsyncSession, user_id: int, status: ListStatus | None = None
) -> list[UserTitle]:
    statement = (
        select(UserTitle)
        .where(UserTitle.user_id == user_id)
        .options(selectinload(UserTitle.title))
        .order_by(UserTitle.updated_at.desc(), UserTitle.id.desc())
    )
    if status is not None:
        statement = statement.where(UserTitle.status == status)
    return list(await session.scalars(statement))


async def count_by_status(session: AsyncSession, user_id: int) -> dict[ListStatus, int]:
    rows = await session.execute(
        select(UserTitle.status, func.count())
        .where(UserTitle.user_id == user_id)
        .group_by(UserTitle.status)
    )
    counts = dict.fromkeys(ListStatus, 0)
    for status, total in rows:
        counts[status] = total
    return counts
