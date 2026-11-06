from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from watchlist.catalog.models import Episode, Season, Title, TitleType
from watchlist.integrations.tmdb import TMDBClient
from watchlist.integrations.tmdb_schemas import (
    SearchResult,
    SeriesWithEpisodes,
    TitleDetails,
)

EPISODE_BATCH_SIZE = 1000


async def search_titles(client: TMDBClient, query: str) -> list[SearchResult]:
    query = query.strip()
    if not query:
        return []
    page = await client.search(query)
    return page.results


async def get_title(session: AsyncSession, title_id: int) -> Title | None:
    statement = (
        select(Title)
        .where(Title.id == title_id)
        .options(selectinload(Title.seasons).selectinload(Season.episodes))
        .execution_options(populate_existing=True)
    )
    return await session.scalar(statement)


async def find_title_id(session: AsyncSession, title_type: TitleType, tmdb_id: int) -> int | None:
    return await session.scalar(
        select(Title.id).where(Title.type == title_type, Title.tmdb_id == tmdb_id)
    )


async def get_or_import_title(
    session: AsyncSession, client: TMDBClient, title_type: TitleType, tmdb_id: int
) -> int:
    existing = await find_title_id(session, title_type, tmdb_id)
    if existing is not None:
        return existing
    return await import_title(session, client, title_type, tmdb_id)


async def import_title(
    session: AsyncSession, client: TMDBClient, title_type: TitleType, tmdb_id: int
) -> int:
    if title_type is TitleType.SERIES:
        return await upsert_series(session, await client.get_series_with_episodes(tmdb_id))
    return await upsert_title(session, TitleType.MOVIE, await client.get_movie(tmdb_id))


async def upsert_title(session: AsyncSession, title_type: TitleType, details: TitleDetails) -> int:
    values: dict[str, Any] = {
        "tmdb_id": details.id,
        "type": title_type,
        "name": details.name,
        "original_name": details.original_name,
        "overview": details.overview,
        "poster_path": details.poster_path,
        "release_date": details.release_date,
        "status": details.status,
        "genres": [genre.name for genre in details.genres],
        "last_synced_at": datetime.now(UTC),
    }
    insert_title = insert(Title).values(values)
    statement = insert_title.on_conflict_do_update(
        index_elements=[Title.tmdb_id, Title.type],
        set_={key: insert_title.excluded[key] for key in values if key not in {"tmdb_id", "type"}},
    ).returning(Title.id)
    title_id: int = (await session.execute(statement)).scalar_one()
    return title_id


async def upsert_series(session: AsyncSession, data: SeriesWithEpisodes) -> int:
    title_id = await upsert_title(session, TitleType.SERIES, data.details)
    if not data.seasons:
        return title_id

    seasons = {season.season_number: season for season in data.seasons}
    season_rows = [
        {
            "title_id": title_id,
            "number": season.season_number,
            "name": season.name,
            "air_date": season.air_date,
        }
        for season in seasons.values()
    ]
    insert_seasons = insert(Season).values(season_rows)
    season_statement = insert_seasons.on_conflict_do_update(
        index_elements=[Season.title_id, Season.number],
        set_={
            "name": insert_seasons.excluded.name,
            "air_date": insert_seasons.excluded.air_date,
        },
    ).returning(Season.id, Season.number)
    season_ids = {
        number: season_id for season_id, number in await session.execute(season_statement)
    }

    episode_rows = {
        (season_ids[season.season_number], episode.episode_number): {
            "season_id": season_ids[season.season_number],
            "number": episode.episode_number,
            "name": episode.name,
            "air_date": episode.air_date,
            "runtime": episode.runtime,
        }
        for season in seasons.values()
        for episode in season.episodes
    }
    await upsert_episodes(session, list(episode_rows.values()))
    return title_id


async def upsert_episodes(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    for start in range(0, len(rows), EPISODE_BATCH_SIZE):
        batch = rows[start : start + EPISODE_BATCH_SIZE]
        statement = insert(Episode).values(list(batch))
        statement = statement.on_conflict_do_update(
            index_elements=[Episode.season_id, Episode.number],
            set_={
                "name": statement.excluded.name,
                "air_date": statement.excluded.air_date,
                "runtime": statement.excluded.runtime,
            },
        )
        await session.execute(statement)
