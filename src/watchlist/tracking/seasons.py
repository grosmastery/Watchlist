from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.catalog.models import Season

RECENT_DAYS = 30
UPCOMING_DAYS = 90


@dataclass(frozen=True, slots=True)
class SeasonNews:
    number: int
    air_date: date
    upcoming: bool

    @property
    def is_premiere(self) -> bool:
        return self.number == 1


async def season_news(
    session: AsyncSession, title_ids: Collection[int], today: date
) -> dict[int, SeasonNews]:
    if not title_ids:
        return {}
    rows = await session.execute(
        select(Season.title_id, Season.number, Season.air_date).where(
            Season.title_id.in_(title_ids),
            Season.number > 0,
            Season.air_date.between(
                today - timedelta(days=RECENT_DAYS), today + timedelta(days=UPCOMING_DAYS)
            ),
        )
    )
    news: dict[int, SeasonNews] = {}
    for title_id, number, air_date in rows:
        if air_date is None:
            continue
        candidate = SeasonNews(number, air_date, upcoming=air_date > today)
        current = news.get(title_id)
        if current is None or is_better(candidate, current):
            news[title_id] = candidate
    return news


def is_better(candidate: SeasonNews, current: SeasonNews) -> bool:
    if candidate.upcoming != current.upcoming:
        return current.upcoming
    if candidate.upcoming:
        return candidate.air_date < current.air_date
    return candidate.number > current.number
