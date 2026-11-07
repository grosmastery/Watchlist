from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.catalog.models import Episode, Season, Title, TitleType

AIRED = date(2020, 1, 1)
TODAY = date(2026, 6, 1)
FUTURE = date(2026, 12, 1)
FAR_FUTURE = date(2999, 1, 1)


@dataclass
class SeriesData:
    title_id: int
    season_ids: list[int]
    episode_ids: list[list[int]]

    def episode(self, season: int, number: int) -> int:
        return self.episode_ids[season - 1][number - 1]

    def season(self, number: int) -> int:
        return self.season_ids[number - 1]


async def create_series(
    session: AsyncSession,
    seasons: Sequence[Sequence[date | None]],
    *,
    name: str = "Серіал",
    tmdb_id: int = 1,
    status: str = "Returning Series",
) -> SeriesData:
    title = Title(
        tmdb_id=tmdb_id,
        type=TitleType.SERIES,
        name=name,
        overview="",
        genres=[],
        status=status,
    )
    session.add(title)
    await session.flush()
    season_ids: list[int] = []
    episode_ids: list[list[int]] = []
    for season_number, air_dates in enumerate(seasons, start=1):
        season = Season(title_id=title.id, number=season_number, name=f"Сезон {season_number}")
        session.add(season)
        await session.flush()
        episodes = [
            Episode(season_id=season.id, number=number, name=f"Серія {number}", air_date=air_date)
            for number, air_date in enumerate(air_dates, start=1)
        ]
        session.add_all(episodes)
        await session.flush()
        season_ids.append(season.id)
        episode_ids.append([episode.id for episode in episodes])
    return SeriesData(title.id, season_ids, episode_ids)
