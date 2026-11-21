from datetime import date

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_user
from tests.factories import create_series
from tests.test_tracking_routes import login
from watchlist.tracking.calendar import Day, Release, calendar_month, grid_bounds, shift_month
from watchlist.tracking.models import ListStatus
from watchlist.tracking.progress import mark_episode
from watchlist.tracking.service import add_to_list

TODAY = date(2026, 11, 21)


def releases_on(calendar_weeks: list[list[Day]], day: date) -> list[Release]:
    return next(item for week in calendar_weeks for item in week if item.date == day).releases


def test_shift_month_crosses_years() -> None:
    assert shift_month(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert shift_month(date(2026, 1, 1), -1) == date(2025, 12, 1)


def test_grid_starts_on_monday_and_ends_on_sunday() -> None:
    start, end = grid_bounds(date(2026, 11, 1))

    assert start == date(2026, 10, 26)
    assert end == date(2026, 12, 6)
    assert start.weekday() == 0
    assert end.weekday() == 6


async def test_calendar_places_episodes_on_air_dates(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(
        db_session,
        [[date(2026, 11, 3), date(2026, 11, 10), date(2026, 12, 20)]],
        name="Серіал",
    )
    await add_to_list(db_session, user_id, series.title_id, ListStatus.WATCHING)
    await mark_episode(db_session, user_id, series.episode(1, 1), TODAY)

    calendar = await calendar_month(db_session, user_id, date(2026, 11, 1), TODAY)

    first = releases_on(calendar.weeks, date(2026, 11, 3))
    second = releases_on(calendar.weeks, date(2026, 11, 10))
    assert [(item.title_name, item.code, item.watched) for item in first] == [
        ("Серіал", "S1 E1", True)
    ]
    assert second[0].watched is False
    assert second[0].episode_name == "Серія 2"
    assert calendar.release_count == 2
    assert calendar.title == "Листопад 2026"
    assert len(calendar.weeks) == 6
    today_cell = next(day for week in calendar.weeks for day in week if day.is_today)
    assert today_cell.date == TODAY


async def test_calendar_groups_episodes_released_together(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    drop = date(2026, 11, 14)
    series = await create_series(db_session, [[drop, drop, drop]], name="Весь сезон")
    await add_to_list(db_session, user_id, series.title_id, ListStatus.PLANNED)

    calendar = await calendar_month(db_session, user_id, date(2026, 11, 1), TODAY)

    releases = releases_on(calendar.weeks, drop)
    assert len(releases) == 1
    assert releases[0].code == "S1 E1–3"
    assert releases[0].episode_name == ""


async def test_calendar_skips_dropped_and_foreign_titles(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session, "me")
    other_id = await create_user(db_session, "other")
    day = date(2026, 11, 5)
    dropped = await create_series(db_session, [[day]], name="Кинутий", tmdb_id=1)
    foreign = await create_series(db_session, [[day]], name="Чужий", tmdb_id=2)
    await add_to_list(db_session, user_id, dropped.title_id, ListStatus.DROPPED)
    await add_to_list(db_session, other_id, foreign.title_id, ListStatus.WATCHING)

    calendar = await calendar_month(db_session, user_id, date(2026, 11, 1), TODAY)

    assert calendar.release_count == 0


async def test_calendar_page_navigates_months(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    series = await create_series(db_session, [[date(2027, 1, 8)]], name="Новорічний")
    await add_to_list(db_session, user_id, series.title_id, ListStatus.WATCHING)

    response = await client.get("/calendar", params={"month": "2027-01"})

    assert response.status_code == 200
    assert "Січень 2027" in response.text
    assert "Новорічний" in response.text
    assert 'href="/calendar?month=2026-12"' in response.text
    assert 'href="/calendar?month=2027-02"' in response.text
    assert 'href="/calendar" class="btn btn-ghost' in response.text


async def test_calendar_page_shows_empty_message(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login(client, db_session)

    response = await client.get("/calendar")

    assert response.status_code == 200
    assert "серій зі списку не виходить" in response.text


async def test_calendar_rejects_bad_month(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    bad_format = await client.get("/calendar", params={"month": "2026-13"})
    bad_year = await client.get("/calendar", params={"month": "0001-01"})

    assert bad_format.status_code == 400
    assert bad_year.status_code == 400


async def test_calendar_requires_login(client: AsyncClient) -> None:
    response = await client.get("/calendar")

    assert response.status_code == 303


async def test_up_next_links_to_calendar(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.get("/up-next")

    assert 'href="/calendar"' in response.text
