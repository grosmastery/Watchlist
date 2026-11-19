from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, register_user
from watchlist.auth.models import User
from watchlist.catalog.models import Season
from watchlist.tracking.models import ListStatus
from watchlist.tracking.progress import utc_today
from watchlist.tracking.seasons import SeasonNews, season_news
from watchlist.tracking.service import add_to_list

TODAY = date(2026, 6, 1)


async def series_with_seasons(
    session: AsyncSession, tmdb_id: int, *air_dates: date | None, name: str = "Серіал"
) -> int:
    title_id = await create_title(session, name=name, tmdb_id=tmdb_id)
    session.add(Season(title_id=title_id, number=0, name="Спецвипуски", air_date=air_dates[-1]))
    session.add_all(
        Season(title_id=title_id, number=number, name=f"Сезон {number}", air_date=air_date)
        for number, air_date in enumerate(air_dates, start=1)
    )
    await session.flush()
    return title_id


def days(count: int, today: date = TODAY) -> date:
    return today + timedelta(days=count)


async def test_recent_season_is_new(db_session: AsyncSession) -> None:
    title_id = await series_with_seasons(db_session, 1, days(-400), days(-10))

    news = await season_news(db_session, [title_id], TODAY)

    assert news == {title_id: SeasonNews(2, days(-10), upcoming=False)}


async def test_aired_season_wins_over_announced_one(db_session: AsyncSession) -> None:
    title_id = await series_with_seasons(db_session, 1, days(-20), days(-5), days(40))

    news = await season_news(db_session, [title_id], TODAY)

    assert news[title_id] == SeasonNews(2, days(-5), upcoming=False)


async def test_nearest_announced_season(db_session: AsyncSession) -> None:
    title_id = await series_with_seasons(db_session, 1, days(-400), days(30), days(80))

    news = await season_news(db_session, [title_id], TODAY)

    assert news[title_id] == SeasonNews(2, days(30), upcoming=True)


async def test_old_far_and_undated_seasons_are_ignored(db_session: AsyncSession) -> None:
    old = await series_with_seasons(db_session, 1, days(-31))
    far = await series_with_seasons(db_session, 2, days(-400), days(91))
    undated = await series_with_seasons(db_session, 3, days(-400), None)

    news = await season_news(db_session, [old, far, undated], TODAY)

    assert news == {}


async def test_no_titles_means_no_news(db_session: AsyncSession) -> None:
    assert await season_news(db_session, [], TODAY) == {}


async def test_list_shows_badges(client: AsyncClient, db_session: AsyncSession) -> None:
    await register_user(client)
    user_id = await db_session.scalar(select(User.id))
    assert user_id is not None
    today = utc_today()
    fresh = await series_with_seasons(
        db_session, 1, days(-400, today), days(-3, today), name="Свіжий"
    )
    soon = await series_with_seasons(db_session, 2, days(-400, today), days(10, today))
    premiere = await series_with_seasons(db_session, 3, today, name="Новинка")
    dropped = await series_with_seasons(db_session, 4, today, name="Кинутий")
    for title_id in (fresh, soon, premiere):
        await add_to_list(db_session, user_id, title_id, ListStatus.WATCHING)
    await add_to_list(db_session, user_id, dropped, ListStatus.DROPPED)

    response = await client.get("/list")

    text = response.text
    assert text.count("Новий сезон") == 1
    assert f"Сезон 2 з {days(10, today).strftime('%d.%m')}" in text
    assert text.count("Прем'єра") == 1


async def test_title_page_shows_badge(client: AsyncClient, db_session: AsyncSession) -> None:
    await register_user(client)
    today = utc_today()
    title_id = await series_with_seasons(db_session, 1, days(-400, today), days(-1, today))

    response = await client.get(f"/titles/{title_id}")

    assert "Новий сезон" in response.text
