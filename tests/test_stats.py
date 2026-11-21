from datetime import date, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, create_user
from tests.factories import AIRED, create_series
from tests.test_tracking_routes import login
from watchlist.catalog.models import Episode, Title
from watchlist.stats.service import last_months, user_stats
from watchlist.tracking.models import ListStatus, WatchedEpisode
from watchlist.tracking.service import add_to_list, update_entry

TODAY = date(2026, 3, 15)


async def watch(
    session: AsyncSession, user_id: int, episode_id: int, when: datetime, runtime: int | None
) -> None:
    episode = await session.get(Episode, episode_id)
    assert episode is not None
    episode.runtime = runtime
    session.add(WatchedEpisode(user_id=user_id, episode_id=episode_id, watched_at=when))
    await session.flush()


async def set_genres(session: AsyncSession, title_id: int, genres: list[str]) -> None:
    title = await session.get(Title, title_id)
    assert title is not None
    title.genres = genres
    await session.flush()


def test_last_months_cross_year_boundary() -> None:
    months = last_months(date(2026, 2, 10), 4)

    assert months == [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]


async def test_user_stats_summarises_list_and_episodes(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [[AIRED, AIRED, AIRED]], tmdb_id=1)
    movie = await create_title(db_session, "Фільм", tmdb_id=2, title_type="movie")
    dropped = await create_title(db_session, "Кинутий", tmdb_id=3)
    await set_genres(db_session, series.title_id, ["Драма", "Кримінал"])
    await set_genres(db_session, movie, ["Драма"])
    await set_genres(db_session, dropped, ["Жахи"])
    await add_to_list(db_session, user_id, series.title_id, ListStatus.WATCHING)
    await add_to_list(db_session, user_id, movie, ListStatus.COMPLETED)
    await add_to_list(db_session, user_id, dropped, ListStatus.DROPPED)
    await update_entry(db_session, user_id, movie, rating=8)
    await update_entry(db_session, user_id, series.title_id, rating=9)
    await watch(db_session, user_id, series.episode(1, 1), datetime(2026, 3, 1, 20), 50)
    await watch(db_session, user_id, series.episode(1, 2), datetime(2026, 3, 2, 20), 70)
    await watch(db_session, user_id, series.episode(1, 3), datetime(2025, 1, 5, 20), None)

    stats = await user_stats(db_session, user_id, TODAY)

    assert (stats.total, stats.series, stats.movies) == (3, 2, 1)
    assert stats.by_status[ListStatus.DROPPED] == 1
    assert (stats.episodes, stats.minutes, stats.hours) == (3, 120, 2)
    assert stats.average_rating == 8.5
    assert [(bar.label, bar.value) for bar in stats.genres] == [("Драма", 2), ("Кримінал", 1)]
    assert stats.genres[0].share == 1.0
    assert len(stats.months) == 12
    assert stats.months[-1].label == "бер"
    assert stats.months[0].label == "кві 25"
    assert stats.months[-1].value == 2
    assert sum(bar.value for bar in stats.months) == 2
    assert {bar.label: bar.value for bar in stats.ratings if bar.value} == {"8": 1, "9": 1}


async def test_user_stats_are_isolated(db_session: AsyncSession) -> None:
    alice = await create_user(db_session, "alice")
    bob = await create_user(db_session, "bob")
    title_id = await create_title(db_session)
    await add_to_list(db_session, alice, title_id, ListStatus.COMPLETED)
    await update_entry(db_session, alice, title_id, rating=10)

    stats = await user_stats(db_session, bob, TODAY)

    assert stats.total == 0
    assert stats.average_rating is None
    assert all(bar.share == 0 for bar in stats.months)


async def test_stats_page_shows_numbers(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await set_genres(db_session, title_id, ["Фантастика"])
    await add_to_list(db_session, user_id, title_id, ListStatus.COMPLETED)
    await update_entry(db_session, user_id, title_id, rating=7)

    response = await client.get("/stats")

    assert response.status_code == 200
    assert "Тайтлів у списку" in response.text
    assert "7.0" in response.text
    assert "Фантастика" in response.text
    assert 'href="/list?genre=%D0%A4' in response.text
    assert 'class="nav-link nav-link-active">Статистика' in response.text


async def test_stats_page_empty_state(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.get("/stats")

    assert "Тут з'явиться статистика" in response.text


async def test_stats_requires_login(client: AsyncClient) -> None:
    response = await client.get("/stats")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
