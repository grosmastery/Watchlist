from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests import tmdb_fakes
from tests.conftest import create_title, create_user, fetch_csrf_token, register_user
from tests.factories import AIRED, TODAY, create_series
from tests.test_catalog_service import mock_series
from watchlist.catalog.models import Episode, Season, Title
from watchlist.integrations.tmdb import TMDBClient
from watchlist.tasks.sync import SyncCandidate, changes_window_start, is_due, sync_titles
from watchlist.tracking.models import ListStatus
from watchlist.tracking.progress import mark_episode, title_progress, watched_ids_for_title
from watchlist.tracking.service import add_to_list

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)
THREE_AIRED = [AIRED, AIRED, AIRED]


def candidate(age: timedelta | None, airing_soon: bool = False, tmdb_id: int = 7) -> SyncCandidate:
    synced = None if age is None else NOW - age
    return SyncCandidate(1, tmdb_id, "Серіал", synced, airing_soon)


def empty_changes(router: respx.MockRouter) -> respx.Route:
    return router.get("/tv/changes").respond(json={"results": [], "page": 1, "total_pages": 1})


async def tracked_series(
    session: AsyncSession, user_id: int, tmdb_id: int, age: timedelta | None = None, **kwargs: str
) -> int:
    series = await create_series(session, [THREE_AIRED], tmdb_id=tmdb_id, **kwargs)
    await add_to_list(session, user_id, series.title_id, ListStatus.WATCHING)
    if age is not None:
        await session.execute(
            update(Title).where(Title.id == series.title_id).values(last_synced_at=NOW - age)
        )
    return series.title_id


@pytest.mark.parametrize(
    ("age", "airing_soon", "changed", "expected"),
    [
        (None, False, False, True),
        (timedelta(hours=1), False, False, False),
        (timedelta(hours=1), True, True, False),
        (timedelta(days=1), False, False, False),
        (timedelta(days=1), True, False, True),
        (timedelta(days=1), False, True, True),
        (timedelta(days=7), False, False, True),
    ],
)
def test_is_due(age: timedelta | None, airing_soon: bool, changed: bool, expected: bool) -> None:
    changed_ids = {7} if changed else set()

    assert is_due(candidate(age, airing_soon), NOW, changed_ids) is expected


def test_changes_window_starts_at_latest_sync_and_is_clamped() -> None:
    recent = [candidate(timedelta(days=3)), candidate(timedelta(days=1))]
    old = [candidate(timedelta(days=60))]

    assert changes_window_start(recent, NOW) == date(2026, 5, 31)
    assert changes_window_start(old, NOW) == date(2026, 5, 18)
    assert changes_window_start([candidate(None)], NOW) == date(2026, 5, 18)


async def test_changed_series_reads_all_pages(
    tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    route = tmdb_mock.get("/tv/changes")
    route.side_effect = [
        httpx.Response(200, json={"results": [{"id": 1}, {"id": 2}], "total_pages": 2}),
        httpx.Response(200, json={"results": [{"id": 3}], "total_pages": 2}),
    ]

    changed = await tmdb_client.get_changed_series(date(2026, 5, 30), date(2026, 6, 1))

    assert changed == {1, 2, 3}
    assert route.calls[0].request.url.params["start_date"] == "2026-05-30"
    assert route.calls[1].request.url.params["page"] == "2"


async def test_sync_adds_new_episodes_and_keeps_progress(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED], tmdb_id=1399)
    await mark_episode(db_session, user_id, series.episode(1, 1), TODAY)
    empty_changes(tmdb_mock)
    mock_series(tmdb_mock, tmdb_id=1399, seasons=2)

    result = await sync_titles(db_session, tmdb_client, NOW)

    assert (result.candidates, result.updated, result.failed) == (1, 1, 0)
    assert await db_session.scalar(select(func.count()).select_from(Season)) == 2
    assert await db_session.scalar(select(func.count()).select_from(Episode)) == 6
    assert await watched_ids_for_title(db_session, user_id, series.title_id) == {
        series.episode(1, 1)
    }
    progress = await title_progress(db_session, user_id, series.title_id, TODAY)
    assert progress.total == 6
    title = await db_session.get(Title, series.title_id, populate_existing=True)
    assert title is not None
    assert title.last_synced_at is not None


async def test_sync_twice_does_not_duplicate(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    user_id = await create_user(db_session)
    await tracked_series(db_session, user_id, 1399)
    empty_changes(tmdb_mock)
    mock_series(tmdb_mock, tmdb_id=1399)

    await sync_titles(db_session, tmdb_client, NOW)
    await db_session.execute(
        update(Title).values(last_synced_at=NOW - timedelta(days=8), status="Returning Series")
    )
    await sync_titles(db_session, tmdb_client, NOW)

    assert await db_session.scalar(select(func.count()).select_from(Season)) == 2
    assert await db_session.scalar(select(func.count()).select_from(Episode)) == 6


async def test_sync_skips_finished_unlisted_and_movies(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    user_id = await create_user(db_session)
    await tracked_series(db_session, user_id, 1, status="Ended")
    await tracked_series(db_session, user_id, 2, status="Canceled")
    await create_series(db_session, [THREE_AIRED], tmdb_id=3)
    movie = await create_title(db_session, "Фільм", tmdb_id=4, title_type="movie")
    await add_to_list(db_session, user_id, movie, ListStatus.WATCHING)
    changes = empty_changes(tmdb_mock)

    result = await sync_titles(db_session, tmdb_client, NOW)

    assert (result.candidates, result.updated, result.failed) == (0, 0, 0)
    assert changes.call_count == 0


async def test_sync_leaves_fresh_series_alone(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    user_id = await create_user(db_session)
    await tracked_series(db_session, user_id, 1399, age=timedelta(days=2))
    empty_changes(tmdb_mock)
    series_route = mock_series(tmdb_mock, tmdb_id=1399)

    result = await sync_titles(db_session, tmdb_client, NOW)

    assert (result.candidates, result.updated) == (1, 0)
    assert series_route.call_count == 0


async def test_sync_updates_series_from_changes_list(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    user_id = await create_user(db_session)
    await tracked_series(db_session, user_id, 1399, age=timedelta(days=2))
    tmdb_mock.get("/tv/changes").respond(json={"results": [{"id": 1399}], "total_pages": 1})
    series_route = mock_series(tmdb_mock, tmdb_id=1399)

    result = await sync_titles(db_session, tmdb_client, NOW)

    assert result.updated == 1
    assert series_route.call_count > 0


async def test_sync_updates_series_with_episode_airing_soon(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    user_id = await create_user(db_session)
    series = await create_series(
        db_session, [[AIRED, NOW.date() + timedelta(days=3)]], tmdb_id=1399
    )
    await add_to_list(db_session, user_id, series.title_id, ListStatus.WATCHING)
    await db_session.execute(update(Title).values(last_synced_at=NOW - timedelta(days=2)))
    empty_changes(tmdb_mock)
    mock_series(tmdb_mock, tmdb_id=1399)

    result = await sync_titles(db_session, tmdb_client, NOW)

    assert result.updated == 1


async def test_one_failure_does_not_stop_the_rest(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    user_id = await create_user(db_session)
    broken = await tracked_series(db_session, user_id, 1)
    working = await tracked_series(db_session, user_id, 2)
    empty_changes(tmdb_mock)
    tmdb_mock.get("/tv/1").respond(500)
    mock_series(tmdb_mock, tmdb_id=2)

    result = await sync_titles(db_session, tmdb_client, NOW)

    assert (result.candidates, result.updated, result.failed) == (2, 1, 1)
    broken_title = await db_session.get(Title, broken, populate_existing=True)
    working_title = await db_session.get(Title, working, populate_existing=True)
    assert broken_title is not None
    assert broken_title.last_synced_at is None
    assert working_title is not None
    assert working_title.last_synced_at is not None


async def test_changes_failure_still_syncs_due_series(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    user_id = await create_user(db_session)
    await tracked_series(db_session, user_id, 1399)
    await tracked_series(db_session, user_id, 2, age=timedelta(days=2))
    changes = tmdb_mock.get("/tv/changes").respond(503)
    mock_series(tmdb_mock, tmdb_id=1399)

    result = await sync_titles(db_session, tmdb_client, NOW)

    assert changes.called
    assert (result.updated, result.failed) == (1, 0)


async def test_changes_list_is_not_requested_when_it_decides_nothing(
    db_session: AsyncSession, tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    user_id = await create_user(db_session)
    await tracked_series(db_session, user_id, 1399, age=timedelta(hours=1))
    changes = empty_changes(tmdb_mock)

    result = await sync_titles(db_session, tmdb_client, NOW)

    assert (result.candidates, result.updated) == (1, 0)
    assert changes.call_count == 0


async def test_refresh_button_updates_title(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    series = await create_series(db_session, [THREE_AIRED], tmdb_id=1399, status="Ended")
    mock_series(tmdb_mock, tmdb_id=1399, seasons=2)
    token = await fetch_csrf_token(client, "/list")

    response = await client.post(f"/titles/{series.title_id}/refresh", data={"csrf_token": token})

    assert response.status_code == 303
    assert response.headers["location"] == f"/titles/{series.title_id}"
    assert await db_session.scalar(select(func.count()).select_from(Season)) == 2


async def test_refresh_movie(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, "Стара назва", tmdb_id=27205, title_type="movie")
    tmdb_mock.get("/movie/27205").respond(json=tmdb_fakes.movie())
    token = await fetch_csrf_token(client, "/list")

    await client.post(f"/titles/{title_id}/refresh", data={"csrf_token": token})

    page = await client.get(f"/titles/{title_id}")
    assert "Початок" in page.text
    assert "Оновлено" in page.text


async def test_refresh_requires_csrf_and_existing_title(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await register_user(client)
    title_id = await create_title(db_session)
    token = await fetch_csrf_token(client, "/list")

    forbidden = await client.post(f"/titles/{title_id}/refresh")
    missing = await client.post("/titles/999999/refresh", data={"csrf_token": token})

    assert forbidden.status_code == 403
    assert missing.status_code == 404
