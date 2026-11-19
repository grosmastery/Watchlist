from dataclasses import dataclass
from enum import Enum, StrEnum, auto
from typing import Any

from sqlalchemy import UnaryExpression, any_, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from watchlist.catalog.models import Title, TitleType
from watchlist.tracking.models import RATING_MAX, RATING_MIN, ListStatus, UserTitle


class ListSort(StrEnum):
    UPDATED = "updated"
    ADDED = "added"
    NAME = "name"
    RATING = "rating"
    YEAR = "year"

    @property
    def label(self) -> str:
        return SORT_LABELS[self]


SORT_LABELS = {
    ListSort.UPDATED: "Нещодавно змінені",
    ListSort.ADDED: "Нещодавно додані",
    ListSort.NAME: "За назвою",
    ListSort.RATING: "За моєю оцінкою",
    ListSort.YEAR: "За роком виходу",
}


@dataclass(frozen=True)
class ListFilters:
    status: ListStatus | None = None
    title_type: TitleType | None = None
    genre: str | None = None
    sort: ListSort = ListSort.UPDATED


def sort_order(sort: ListSort) -> list[UnaryExpression[Any]]:
    orders: dict[ListSort, list[UnaryExpression[Any]]] = {
        ListSort.UPDATED: [UserTitle.updated_at.desc()],
        ListSort.ADDED: [UserTitle.added_at.desc()],
        ListSort.NAME: [func.lower(Title.name).asc()],
        ListSort.RATING: [UserTitle.rating.desc().nulls_last(), UserTitle.updated_at.desc()],
        ListSort.YEAR: [Title.release_date.desc().nulls_last()],
    }
    return [*orders[sort], UserTitle.id.desc()]


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


async def rate_title(
    session: AsyncSession, user_id: int, title_id: int, rating: int | None
) -> UserTitle | None:
    validate_rating(rating)
    if await get_entry(session, user_id, title_id) is None:
        if rating is None:
            return None
        await add_to_list(session, user_id, title_id, ListStatus.COMPLETED)
    return await update_entry(session, user_id, title_id, rating=rating)


async def remove_from_list(session: AsyncSession, user_id: int, title_id: int) -> bool:
    result = await session.execute(
        delete(UserTitle)
        .where(UserTitle.user_id == user_id, UserTitle.title_id == title_id)
        .returning(UserTitle.id)
    )
    return result.first() is not None


async def list_entries(
    session: AsyncSession, user_id: int, filters: ListFilters | None = None
) -> list[UserTitle]:
    filters = filters or ListFilters()
    statement = (
        select(UserTitle)
        .join(UserTitle.title)
        .where(UserTitle.user_id == user_id)
        .options(selectinload(UserTitle.title))
        .order_by(*sort_order(filters.sort))
    )
    if filters.status is not None:
        statement = statement.where(UserTitle.status == filters.status)
    if filters.title_type is not None:
        statement = statement.where(Title.type == filters.title_type)
    if filters.genre:
        statement = statement.where(any_(Title.genres) == filters.genre)
    return list(await session.scalars(statement))


async def list_genres(session: AsyncSession, user_id: int) -> list[str]:
    genre = func.unnest(Title.genres).label("genre")
    subquery = (
        select(genre)
        .join(UserTitle, UserTitle.title_id == Title.id)
        .where(UserTitle.user_id == user_id)
    ).subquery()
    rows = await session.scalars(select(subquery.c.genre).distinct())
    return sorted(rows, key=str.casefold)


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
