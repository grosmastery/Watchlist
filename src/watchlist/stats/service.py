from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.catalog.models import Episode, Title, TitleType
from watchlist.tracking.models import RATING_MAX, RATING_MIN, ListStatus, UserTitle, WatchedEpisode
from watchlist.tracking.service import count_by_status

MONTHS = 12
TOP_GENRES = 8
MONTH_NAMES = ("січ", "лют", "бер", "кві", "тра", "чер", "лип", "сер", "вер", "жов", "лис", "гру")


@dataclass(frozen=True, slots=True)
class Bar:
    label: str
    value: int
    share: float


@dataclass(frozen=True, slots=True)
class UserStats:
    total: int
    series: int
    movies: int
    by_status: dict[ListStatus, int]
    episodes: int
    minutes: int
    rated: int
    average_rating: float | None
    ratings: list[Bar]
    genres: list[Bar]
    months: list[Bar]

    @property
    def hours(self) -> int:
        return round(self.minutes / 60)

    @property
    def days(self) -> float:
        return self.minutes / 60 / 24


def bars(items: list[tuple[str, int]]) -> list[Bar]:
    peak = max((value for _, value in items), default=0)
    return [Bar(label, value, value / peak if peak else 0.0) for label, value in items]


def last_months(today: date, count: int = MONTHS) -> list[date]:
    index = today.year * 12 + today.month - 1
    return [
        date(year, month + 1, 1)
        for year, month in (divmod(index - offset, 12) for offset in range(count - 1, -1, -1))
    ]


def month_label(month: date, today: date) -> str:
    name = MONTH_NAMES[month.month - 1]
    return name if month.year == today.year else f"{name} {month.year % 100:02d}"


async def type_counts(session: AsyncSession, user_id: int) -> dict[TitleType, int]:
    rows = await session.execute(
        select(Title.type, func.count())
        .join(UserTitle, UserTitle.title_id == Title.id)
        .where(UserTitle.user_id == user_id)
        .group_by(Title.type)
    )
    counts = dict.fromkeys(TitleType, 0)
    for title_type, total in rows:
        counts[title_type] = total
    return counts


async def episode_totals(session: AsyncSession, user_id: int) -> tuple[int, int]:
    episodes, minutes = (
        await session.execute(
            select(func.count(), func.sum(Episode.runtime))
            .select_from(WatchedEpisode)
            .join(Episode, Episode.id == WatchedEpisode.episode_id)
            .where(WatchedEpisode.user_id == user_id)
        )
    ).one()
    return episodes, minutes or 0


async def rating_counts(session: AsyncSession, user_id: int) -> dict[int, int]:
    rows = await session.execute(
        select(UserTitle.rating, func.count())
        .where(UserTitle.user_id == user_id, UserTitle.rating.is_not(None))
        .group_by(UserTitle.rating)
    )
    counts = dict.fromkeys(range(RATING_MIN, RATING_MAX + 1), 0)
    for rating, total in rows:
        if rating is not None:
            counts[rating] = total
    return counts


async def genre_counts(session: AsyncSession, user_id: int) -> list[tuple[str, int]]:
    genre = func.unnest(Title.genres).label("genre")
    subquery = (
        select(genre)
        .join(UserTitle, UserTitle.title_id == Title.id)
        .where(UserTitle.user_id == user_id, UserTitle.status != ListStatus.DROPPED)
    ).subquery()
    rows = await session.execute(
        select(subquery.c.genre, func.count().label("total"))
        .group_by(subquery.c.genre)
        .order_by(func.count().desc(), subquery.c.genre)
        .limit(TOP_GENRES)
    )
    return [(name, total) for name, total in rows]


async def monthly_episodes(session: AsyncSession, user_id: int, today: date) -> list[Bar]:
    months = last_months(today)
    month = func.date_trunc("month", WatchedEpisode.watched_at).label("month")
    rows = await session.execute(
        select(month, func.count())
        .where(WatchedEpisode.user_id == user_id, WatchedEpisode.watched_at >= months[0])
        .group_by(month)
    )
    counts = {value.date(): total for value, total in rows}
    return bars([(month_label(item, today), counts.get(item, 0)) for item in months])


async def user_stats(session: AsyncSession, user_id: int, today: date) -> UserStats:
    types = await type_counts(session, user_id)
    episodes, minutes = await episode_totals(session, user_id)
    ratings = await rating_counts(session, user_id)
    rated = sum(ratings.values())
    weighted = sum(rating * total for rating, total in ratings.items())
    return UserStats(
        total=sum(types.values()),
        series=types[TitleType.SERIES],
        movies=types[TitleType.MOVIE],
        by_status=await count_by_status(session, user_id),
        episodes=episodes,
        minutes=minutes,
        rated=rated,
        average_rating=weighted / rated if rated else None,
        ratings=bars([(str(rating), total) for rating, total in ratings.items()]),
        genres=bars(await genre_counts(session, user_id)),
        months=await monthly_episodes(session, user_id, today),
    )
