import httpx
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests import tmdb_fakes
from watchlist.catalog.models import Episode, Season, Title, TitleType
from watchlist.catalog.service import get_or_import_title, get_title, import_title
from watchlist.integrations.tmdb import TMDBClient


def mock_series(router: respx.MockRouter, tmdb_id: int = 1399, seasons: int = 2) -> respx.Route:
    def respond(request: httpx.Request) -> httpx.Response:
        append = request.url.params.get("append_to_response")
        if not append:
            return httpx.Response(200, json=tmdb_fakes.series(tmdb_id, seasons=seasons))
        numbers = [int(part.split("/")[1]) for part in append.split(",")]
        return httpx.Response(200, json=tmdb_fakes.series_with_seasons(tmdb_id, numbers))

    return router.get(f"/tv/{tmdb_id}").mock(side_effect=respond)


async def count(session: AsyncSession, model: type[Title | Season | Episode]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_import_series_with_seasons_and_episodes(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    mock_series(tmdb_mock)

    title_id = await import_title(db_session, tmdb_client, TitleType.SERIES, 1399)
    title = await get_title(db_session, title_id)

    assert title is not None
    assert title.type is TitleType.SERIES
    assert title.name == "Гра престолів"
    assert title.original_name == "Game of Thrones"
    assert title.genres == ["Драма"]
    assert title.last_synced_at is not None
    assert [season.number for season in title.seasons] == [1, 2]
    assert [episode.number for episode in title.seasons[0].episodes] == [1, 2, 3]


async def test_reimport_does_not_duplicate(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    mock_series(tmdb_mock)

    first = await import_title(db_session, tmdb_client, TitleType.SERIES, 1399)
    second = await import_title(db_session, tmdb_client, TitleType.SERIES, 1399)

    assert first == second
    assert await count(db_session, Title) == 1
    assert await count(db_session, Season) == 2
    assert await count(db_session, Episode) == 6


async def test_reimport_updates_changed_data(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    mock_series(tmdb_mock, seasons=1)
    title_id = await import_title(db_session, tmdb_client, TitleType.SERIES, 1399)

    tmdb_mock.routes.clear()
    mock_series(tmdb_mock, seasons=2)
    await import_title(db_session, tmdb_client, TitleType.SERIES, 1399)
    title = await get_title(db_session, title_id)

    assert title is not None
    assert [season.number for season in title.seasons] == [1, 2]
    assert await count(db_session, Episode) == 6


async def test_get_or_import_uses_catalog_when_title_exists(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    route = mock_series(tmdb_mock)
    first = await get_or_import_title(db_session, tmdb_client, TitleType.SERIES, 1399)
    calls = route.call_count

    second = await get_or_import_title(db_session, tmdb_client, TitleType.SERIES, 1399)

    assert first == second
    assert route.call_count == calls


async def test_import_movie_has_no_seasons(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    tmdb_mock.get("/movie/27205").respond(json=tmdb_fakes.movie())

    title_id = await import_title(db_session, tmdb_client, TitleType.MOVIE, 27205)
    title = await get_title(db_session, title_id)

    assert title is not None
    assert title.type is TitleType.MOVIE
    assert title.name == "Початок"
    assert title.seasons == []


async def test_series_and_movie_can_share_tmdb_id(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    mock_series(tmdb_mock, tmdb_id=100)
    tmdb_mock.get("/movie/100").respond(json=tmdb_fakes.movie(tmdb_id=100))

    series_id = await import_title(db_session, tmdb_client, TitleType.SERIES, 100)
    movie_id = await import_title(db_session, tmdb_client, TitleType.MOVIE, 100)

    assert series_id != movie_id
    assert await count(db_session, Title) == 2
