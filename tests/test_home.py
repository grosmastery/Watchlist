from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, register_user
from watchlist.auth.models import User
from watchlist.catalog.models import Title
from watchlist.home.cache import TTLCache
from watchlist.home.feeds import Card, interleave, new_season_number
from watchlist.home.recommendations import (
    PER_SOURCE_LIMIT,
    Signal,
    genre_factor,
    genre_profile,
    rank,
    select_seeds,
    signal_weight,
)
from watchlist.integrations.tmdb import TMDBClient, TMDBUnavailableError
from watchlist.integrations.tmdb_schemas import BrowseItem, MediaType, SeasonSummary
from watchlist.tracking.models import ListStatus
from watchlist.tracking.service import add_to_list, update_entry

TODAY = datetime.now(UTC).date()
RECENT = (TODAY - timedelta(days=10)).isoformat()
OLD = "2015-01-01"
UPDATED = datetime(2026, 1, 1)
HTMX = {"HX-Request": "true"}


def tv(tmdb_id: int, name: str, first_air: str = OLD, **extra: Any) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "name": name,
        "original_name": name,
        "original_language": "uk",
        "first_air_date": first_air,
        "poster_path": f"/{tmdb_id}.jpg",
        "genre_ids": [18],
        "popularity": 10.0,
        "vote_count": 100,
        **extra,
    }


def film(tmdb_id: int, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "title": name,
        "original_title": name,
        "original_language": "uk",
        "release_date": RECENT,
        "poster_path": f"/{tmdb_id}.jpg",
        "genre_ids": [28],
        "popularity": 10.0,
        "vote_count": 100,
        **extra,
    }


def anime_tv(tmdb_id: int, name: str, first_air: str = OLD) -> dict[str, Any]:
    return tv(tmdb_id, name, first_air, genre_ids=[16], original_language="ja")


def page(*results: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"page": 1, "total_pages": 1, "results": list(results)})


def seasons_payload(tmdb_id: int, *numbers_and_dates: tuple[int, str]) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "seasons": [
            {"season_number": number, "air_date": air_date, "episode_count": 8}
            for number, air_date in numbers_and_dates
        ],
    }


def browse(
    tmdb_id: int, *genre_ids: int, media_type: MediaType = "tv", votes: int = 100
) -> BrowseItem:
    return BrowseItem(
        id=tmdb_id,
        media_type=media_type,
        name=f"Тайтл {tmdb_id}",
        poster_path="/p.jpg",
        vote_count=votes,
        genre_ids=list(genre_ids),
    )


def signal(tmdb_id: int, weight: float, *genres: str, updated: datetime = UPDATED) -> Signal:
    return Signal(("tv", tmdb_id), f"Джерело {tmdb_id}", tuple(genres), weight, updated)


def card(tmdb_id: int) -> Card:
    return Card("tv", tmdb_id, str(tmdb_id), "/p.jpg", "")


@pytest.mark.parametrize(
    ("status", "rating", "expected"),
    [
        (ListStatus.COMPLETED, 10, 3.0),
        (ListStatus.COMPLETED, 9, 3.0),
        (ListStatus.WATCHING, 8, 2.0),
        (ListStatus.COMPLETED, 7, 2.0),
        (ListStatus.COMPLETED, 6, 0.0),
        (ListStatus.COMPLETED, 5, 0.0),
        (ListStatus.COMPLETED, 4, -2.0),
        (ListStatus.COMPLETED, None, 1.0),
        (ListStatus.WATCHING, None, 1.0),
        (ListStatus.PLANNED, None, 0.5),
        (ListStatus.DROPPED, None, -2.0),
        (ListStatus.DROPPED, 10, -2.0),
    ],
)
def test_signal_weight(status: ListStatus, rating: int | None, expected: float) -> None:
    assert signal_weight(status, rating) == expected


def test_seeds_are_positive_strongest_then_recent() -> None:
    newer = signal(2, 3.0, updated=UPDATED + timedelta(days=1))
    signals = [signal(1, 3.0), newer, signal(3, 0.0), signal(4, -2.0), signal(5, 0.5)]

    assert [seed.key[1] for seed in select_seeds(signals)] == [2, 1, 5]


def test_genre_profile_lowers_score_of_disliked_genres() -> None:
    profile = genre_profile([signal(1, 3.0, "Драма"), signal(2, -2.0, "Комедія")])

    assert genre_factor(["Драма"], profile) == 1.5
    assert genre_factor(["Комедія"], profile) < 1
    assert genre_factor([], profile) == 1


def test_rank_sums_sources_and_excludes_known_titles() -> None:
    first, second = signal(1, 3.0), signal(2, 2.0)
    results = [
        (first, [browse(100), browse(200), browse(2)]),
        (second, [browse(200), browse(300, votes=1)]),
    ]

    cards = rank(results, [first, second], {"tv": {}, "movie": {}})

    assert [item.tmdb_id for item in cards] == [200, 100]
    assert cards[1].note == "Бо вам сподобалось «Джерело 1»"


def test_rank_limits_titles_from_one_source() -> None:
    seed = signal(1, 3.0)
    results = [(seed, [browse(100 + index) for index in range(10)])]

    assert len(rank(results, [seed], {"tv": {}, "movie": {}})) == PER_SOURCE_LIMIT


def test_rank_explains_weak_source_as_similar() -> None:
    seed = signal(1, 1.0)

    cards = rank([(seed, [browse(100)])], [seed], {"tv": {}, "movie": {}})

    assert cards[0].note == "Схоже на «Джерело 1»"


def test_rank_prefers_liked_genres() -> None:
    seed = signal(1, 3.0, "Драма")
    disliked = signal(2, -2.0, "Комедія")
    names: dict[MediaType, dict[int, str]] = {"tv": {18: "Драма", 35: "Комедія"}, "movie": {}}

    cards = rank([(seed, [browse(100, 35), browse(200, 18)])], [seed, disliked], names)

    assert [item.tmdb_id for item in cards] == [200, 100]


def test_interleave_alternates_and_removes_duplicates() -> None:
    result = interleave([card(1), card(2), card(3)], [card(1), card(4)], limit=3)

    assert [item.tmdb_id for item in result] == [1, 2, 4]


def test_new_season_number() -> None:
    start, today = date(2026, 8, 1), date(2026, 11, 1)
    seasons = [
        SeasonSummary(season_number=0, air_date=date(2026, 9, 1)),
        SeasonSummary(season_number=1, air_date=date(2026, 9, 1)),
        SeasonSummary(season_number=2, air_date=date(2026, 9, 2)),
        SeasonSummary(season_number=3, air_date=date(2026, 9, 3)),
        SeasonSummary(season_number=4, air_date=date(2027, 1, 1)),
    ]

    assert new_season_number(seasons, start, today) == 3
    assert new_season_number(seasons[:2], start, today) is None


async def test_cache_reuses_value_until_expired() -> None:
    now = [0.0]
    cache = TTLCache(clock=lambda: now[0])
    calls: list[int] = []

    async def load() -> int:
        calls.append(1)
        return len(calls)

    assert await cache.get_or_load("key", 10, load) == 1
    assert await cache.get_or_load("key", 10, load) == 1
    now[0] = 11
    assert await cache.get_or_load("key", 10, load) == 2


async def test_cache_serves_stale_value_when_tmdb_fails() -> None:
    now = [0.0]
    cache = TTLCache(clock=lambda: now[0])

    async def good() -> str:
        return "old"

    async def broken() -> str:
        raise TMDBUnavailableError("down")

    await cache.get_or_load("key", 10, good)
    now[0] = 11

    assert await cache.get_or_load("key", 10, broken) == "old"
    with pytest.raises(TMDBUnavailableError):
        await cache.get_or_load("other", 10, broken)


async def test_cache_evicts_oldest_entries() -> None:
    cache = TTLCache(max_entries=1)
    values = iter(["a", "b", "c"])

    async def load() -> str:
        return next(values)

    await cache.get_or_load("first", 10, load)
    await cache.get_or_load("second", 10, load)

    assert await cache.get_or_load("first", 10, load) == "c"


async def test_browse_uses_english_name_when_translation_is_missing(
    tmdb_client: TMDBClient, tmdb_mock: respx.MockRouter
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        english = request.url.params["language"] == "en-US"
        name = "Dragon Ball" if english else "ドラゴンボール"
        return page(tv(1, name, original_name="ドラゴンボール", original_language="ja"))

    tmdb_mock.get("/trending/tv/week").mock(side_effect=respond)

    items = await tmdb_client.trending("tv")

    assert items[0].name == "Dragon Ball"
    assert items[0].media_type == "tv"


async def test_browse_keeps_translated_name_without_extra_request(
    tmdb_client: TMDBClient, tmdb_mock: respx.MockRouter
) -> None:
    route = tmdb_mock.get("/trending/tv/week").mock(
        return_value=page(tv(1, "Драконячі перли", original_name="X", original_language="ja"))
    )

    items = await tmdb_client.trending("tv")

    assert items[0].name == "Драконячі перли"
    assert route.call_count == 1


async def test_guest_home_has_feeds_but_no_recommendations(client: AsyncClient) -> None:
    response = await client.get("/")

    assert 'hx-get="/home/new"' in response.text
    assert 'hx-get="/home/popular"' in response.text
    assert "/home/recommendations" not in response.text
    assert 'id="nav-search"' in response.text


async def test_logged_in_home_requests_recommendations(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.get("/")

    assert 'hx-get="/home/recommendations"' in response.text
    assert 'href="/up-next"' in response.text


def discover_tv(
    premieres: list[dict[str, Any]], airing: list[dict[str, Any]]
) -> Callable[[httpx.Request], httpx.Response]:
    def respond(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "first_air_date.gte" in params:
            return page(*premieres)
        if "air_date.gte" in params and params.get("page") == "1":
            return page(*airing)
        return page()

    return respond


async def test_new_series_marks_premieres_and_new_seasons(
    client: AsyncClient, tmdb_mock: respx.MockRouter
) -> None:
    tmdb_mock.get("/discover/tv").mock(
        side_effect=discover_tv(
            [tv(1, "Свіжий серіал", RECENT), anime_tv(2, "Прем'єра аніме", RECENT)],
            [tv(3, "Старий серіал"), tv(4, "Без нового сезону"), tv(5, "Ламаний")],
        )
    )
    tmdb_mock.get("/tv/3").respond(json=seasons_payload(3, (1, OLD), (3, RECENT)))
    tmdb_mock.get("/tv/4").respond(json=seasons_payload(4, (1, OLD), (2, "2020-01-01")))
    tmdb_mock.get("/tv/5").respond(500)

    response = await client.get("/home/new", headers=HTMX)

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Свіжий серіал" in response.text
    assert "Прем&#39;єра" in response.text
    assert "Старий серіал" in response.text
    assert "Новий сезон" in response.text
    assert "3 сезон" in response.text
    assert "Без нового сезону" not in response.text
    assert "Ламаний" not in response.text
    assert "Прем&#39;єра аніме" not in response.text
    assert 'href="/tmdb/tv/3"' in response.text


async def test_new_series_excludes_talk_shows_through_discover_filter(
    client: AsyncClient, tmdb_mock: respx.MockRouter
) -> None:
    route = tmdb_mock.get("/discover/tv").mock(return_value=page())

    await client.get("/home/new", headers=HTMX)

    for call in route.calls:
        assert call.request.url.params["without_genres"] == "10763,10764,10766,10767"


async def test_new_movies_tab_skips_anime(client: AsyncClient, tmdb_mock: respx.MockRouter) -> None:
    tmdb_mock.get("/discover/movie").mock(
        return_value=page(
            film(1, "Новий фільм"),
            film(2, "Аніме-фільм", genre_ids=[16], original_language="ja"),
            film(3, "Без постера", poster_path=None),
        )
    )

    response = await client.get("/home/new", params={"tab": "movies"}, headers=HTMX)

    assert "Новий фільм" in response.text
    assert "Аніме-фільм" not in response.text
    assert "Без постера" not in response.text
    assert 'aria-pressed="true"' in response.text


async def test_new_anime_mixes_series_seasons_and_movies(
    client: AsyncClient, tmdb_mock: respx.MockRouter
) -> None:
    tv_route = tmdb_mock.get("/discover/tv").mock(
        side_effect=discover_tv([anime_tv(1, "Нове аніме", RECENT)], [anime_tv(2, "Другий сезон")])
    )
    movie_route = tmdb_mock.get("/discover/movie").mock(
        return_value=page(film(3, "Повнометражне", genre_ids=[16], original_language="ja"))
    )
    tmdb_mock.get("/tv/2").respond(json=seasons_payload(2, (1, OLD), (2, RECENT)))

    response = await client.get("/home/new", params={"tab": "anime"}, headers=HTMX)

    assert "Нове аніме" in response.text
    assert "Другий сезон" in response.text
    assert "Повнометражне" in response.text
    for call in [*tv_route.calls, *movie_route.calls]:
        assert call.request.url.params["with_genres"] == "16"
        assert call.request.url.params["with_original_language"] == "ja"


async def test_feed_is_cached(client: AsyncClient, tmdb_mock: respx.MockRouter) -> None:
    route = tmdb_mock.get("/discover/movie").mock(return_value=page(film(1, "Фільм")))

    await client.get("/home/new", params={"tab": "movies"}, headers=HTMX)
    await client.get("/home/new", params={"tab": "movies"}, headers=HTMX)

    assert route.call_count == 1


async def test_feed_shows_message_when_tmdb_is_down(
    client: AsyncClient, tmdb_mock: respx.MockRouter
) -> None:
    tmdb_mock.get("/discover/movie").respond(503)

    response = await client.get("/home/new", params={"tab": "movies"}, headers=HTMX)

    assert response.status_code == 200
    assert "Не вдалося завантажити" in response.text


async def test_unknown_tab_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/home/new", params={"tab": "cartoons"})

    assert response.status_code == 400


async def test_popular_series_filters_anime_and_talk_shows(
    client: AsyncClient, tmdb_mock: respx.MockRouter
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "1":
            return page(tv(1, "Популярний"), anime_tv(2, "Популярне аніме"))
        return page(tv(3, "Ток-шоу", genre_ids=[10767]))

    tmdb_mock.get("/trending/tv/week").mock(side_effect=respond)

    response = await client.get("/home/popular", headers=HTMX)

    assert "Популярний" in response.text
    assert "Популярне аніме" not in response.text
    assert "Ток-шоу" not in response.text


async def test_popular_anime_is_sorted_by_popularity(
    client: AsyncClient, tmdb_mock: respx.MockRouter
) -> None:
    tmdb_mock.get("/discover/tv").mock(return_value=page(anime_tv(1, "Менш відоме")))
    tmdb_mock.get("/discover/movie").mock(
        return_value=page(
            film(2, "Топове", genre_ids=[16], original_language="ja", popularity=99.0)
        )
    )

    response = await client.get("/home/popular", params={"tab": "anime"}, headers=HTMX)

    assert response.text.index("Топове") < response.text.index("Менш відоме")


async def login(client: AsyncClient, session: AsyncSession) -> int:
    await register_user(client)
    user_id = await session.scalar(select(User.id).where(User.username == "viewer"))
    assert user_id is not None
    return user_id


def mock_genres(tmdb_mock: respx.MockRouter) -> None:
    tmdb_mock.get("/genre/tv/list").respond(json={"genres": [{"id": 18, "name": "Драма"}]})
    tmdb_mock.get("/genre/movie/list").respond(json={"genres": [{"id": 28, "name": "Бойовик"}]})


async def add_rated(
    session: AsyncSession, user_id: int, tmdb_id: int, name: str, rating: int
) -> None:
    title_id = await create_title(session, name, tmdb_id)
    title = await session.get(Title, title_id)
    assert title is not None
    title.genres = ["Драма"]
    await add_to_list(session, user_id, title_id, ListStatus.COMPLETED)
    await update_entry(session, user_id, title_id, rating=rating)


async def test_recommendations_require_login(client: AsyncClient) -> None:
    response = await client.get("/home/recommendations", headers=HTMX)

    assert response.headers["HX-Redirect"].startswith("/login")


async def test_recommendations_hint_for_empty_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login(client, db_session)

    response = await client.get("/home/recommendations", headers=HTMX)

    assert "Додайте кілька тайтлів" in response.text


async def test_recommendations_skip_listed_titles_and_explain_source(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    user_id = await login(client, db_session)
    await add_rated(db_session, user_id, 10, "Улюблений", 9)
    await add_rated(db_session, user_id, 11, "Теж у списку", 2)
    mock_genres(tmdb_mock)
    tmdb_mock.get("/tv/10/recommendations").respond(
        json={"results": [tv(11, "Теж у списку"), tv(20, "Рекомендований")]}
    )
    tmdb_mock.get("/discover/tv").mock(return_value=page())
    tmdb_mock.get("/discover/movie").mock(return_value=page())

    response = await client.get("/home/recommendations", headers=HTMX)

    assert "Рекомендований" in response.text
    assert "Бо вам сподобалось «Улюблений»" in response.text
    assert 'href="/tmdb/tv/11"' not in response.text


async def test_recommendations_refresh_when_list_changes(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    user_id = await login(client, db_session)
    await add_rated(db_session, user_id, 10, "Улюблений", 9)
    mock_genres(tmdb_mock)
    route = tmdb_mock.get("/tv/10/recommendations").respond(json={"results": [tv(20, "Перший")]})
    tmdb_mock.get("/discover/tv").mock(return_value=page())
    tmdb_mock.get("/discover/movie").mock(return_value=page())

    await client.get("/home/recommendations", headers=HTMX)
    await client.get("/home/recommendations", headers=HTMX)
    assert route.call_count == 1

    await add_rated(db_session, user_id, 30, "Новий у списку", 8)
    tmdb_mock.get("/tv/30/recommendations").respond(json={"results": []})
    await client.get("/home/recommendations", headers=HTMX)

    assert route.call_count == 2


async def test_recommendations_fall_back_to_favourite_genres(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    user_id = await login(client, db_session)
    await add_rated(db_session, user_id, 10, "Улюблений", 9)
    mock_genres(tmdb_mock)
    tmdb_mock.get("/tv/10/recommendations").respond(json={"results": []})
    discover = tmdb_mock.get("/discover/tv").mock(return_value=page(tv(40, "Жанровий хіт")))

    response = await client.get("/home/recommendations", headers=HTMX)

    assert "Жанровий хіт" in response.text
    assert "Популярне в жанрі «Драма»" in response.text
    assert discover.calls[0].request.url.params["with_genres"] == "18"


async def test_recommendations_show_message_when_every_seed_fails(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    user_id = await login(client, db_session)
    await add_rated(db_session, user_id, 10, "Улюблений", 9)
    mock_genres(tmdb_mock)
    tmdb_mock.get("/tv/10/recommendations").respond(503)

    response = await client.get("/home/recommendations", headers=HTMX)

    assert response.status_code == 200
    assert "Не вдалося завантажити" in response.text
