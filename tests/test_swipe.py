from typing import Any

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, fetch_csrf_token, register_user
from watchlist.auth.models import User
from watchlist.integrations.tmdb import TMDBClient
from watchlist.tracking.models import ListStatus
from watchlist.tracking.service import add_to_list

HTMX = {"HX-Request": "true"}


def tv(tmdb_id: int, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "name": name,
        "original_name": name,
        "original_language": "uk",
        "first_air_date": "2019-01-01",
        "overview": "Опис серіалу",
        "poster_path": f"/{tmdb_id}.jpg",
        "genre_ids": [18],
        "vote_average": 8.25,
        "vote_count": 500,
        **extra,
    }


def film(tmdb_id: int, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "title": name,
        "original_title": name,
        "original_language": "uk",
        "release_date": "2020-01-01",
        "overview": "Опис фільму",
        "poster_path": f"/{tmdb_id}.jpg",
        "genre_ids": [28],
        "vote_average": 7.0,
        "vote_count": 500,
        **extra,
    }


def anime(tmdb_id: int, name: str) -> dict[str, Any]:
    return tv(tmdb_id, name, genre_ids=[16], original_language="ja", original_name="元")


def page(*results: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"page": 1, "total_pages": 1, "results": list(results)})


def mock_genres(tmdb_mock: respx.MockRouter) -> None:
    tmdb_mock.get("/genre/tv/list").mock(
        return_value=httpx.Response(200, json={"genres": [{"id": 18, "name": "Драма"}]})
    )
    tmdb_mock.get("/genre/movie/list").mock(
        return_value=httpx.Response(200, json={"genres": [{"id": 28, "name": "Бойовик"}]})
    )


async def login(client: AsyncClient, session: AsyncSession, username: str = "viewer") -> int:
    await register_user(client, email=f"{username}@example.com", username=username)
    user_id = await session.scalar(select(User.id).where(User.username == username))
    assert user_id is not None
    return user_id


async def csrf_headers(client: AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": await fetch_csrf_token(client, "/")}


def decision(tmdb_id: int = 1, *, liked: bool = True, **extra: str) -> dict[str, Any]:
    return {
        "media_type": "tv",
        "tmdb_id": tmdb_id,
        "liked": str(liked).lower(),
        "name": f"Тайтл {tmdb_id}",
        "poster_path": f"/{tmdb_id}.jpg",
        "subtitle": "Серіал · 2019",
        **extra,
    }


async def test_swipe_requires_login(client: AsyncClient) -> None:
    response = await client.get("/swipe")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


async def test_page_selects_all_categories_by_default(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login(client, db_session)

    response = await client.get("/swipe")

    assert response.text.count("checked") == 3


async def test_page_keeps_chosen_categories(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.get("/swipe", params={"types": ["anime", "movies"]})

    assert response.text.count("checked") == 2
    assert 'value="series" class="sr-only" >' in response.text


async def test_deck_uses_only_chosen_category(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await login(client, db_session)
    mock_genres(tmdb_mock)
    movies = tmdb_mock.get("/discover/movie").mock(return_value=page(film(1, "Фільм один")))
    series = tmdb_mock.get("/discover/tv").mock(return_value=page(tv(2, "Серіал два")))

    response = await client.get("/swipe/deck", params={"types": "movies"}, headers=HTMX)

    assert "Фільм один" in response.text
    assert "Серіал два" not in response.text
    assert "★ 7.0" in response.text
    assert "Бойовик" in response.text
    assert movies.called
    assert not series.called
    assert response.headers["HX-Replace-Url"] == "/swipe?types=movies"


async def test_deck_mixes_two_categories_and_filters_anime_from_series(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await login(client, db_session)
    mock_genres(tmdb_mock)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("with_genres") == "16":
            return page(anime(3, "Справжнє аніме"))
        return page(tv(2, "Звичайний серіал"), anime(4, "Аніме серед серіалів"))

    tmdb_mock.get("/discover/tv").mock(side_effect=respond)
    tmdb_mock.get("/discover/movie").mock(return_value=page())

    response = await client.get("/swipe/deck", params={"types": ["series", "anime"]}, headers=HTMX)

    assert "Звичайний серіал" in response.text
    assert "Справжнє аніме" in response.text
    assert "Аніме серед серіалів" not in response.text


async def test_deck_skips_swiped_and_listed_titles(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    user_id = await login(client, db_session)
    mock_genres(tmdb_mock)
    title_id = await create_title(db_session, name="У списку", tmdb_id=20)
    await add_to_list(db_session, user_id, title_id, ListStatus.PLANNED)
    await client.post(
        "/swipe/decisions", data=decision(10, liked=False), headers=await csrf_headers(client)
    )
    tmdb_mock.get("/discover/tv").mock(
        return_value=page(tv(10, "Пропущений"), tv(20, "У списку"), tv(30, "Новий"))
    )

    response = await client.get("/swipe/deck", params={"types": "series"}, headers=HTMX)

    assert "Новий" in response.text
    assert "Пропущений" not in response.text
    assert 'data-tmdb-id="20"' not in response.text


async def test_deck_asks_for_category_without_calling_tmdb(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await login(client, db_session)

    response = await client.get("/swipe/deck", headers=HTMX)

    assert "Оберіть хоча б одну категорію" in response.text
    assert "HX-Replace-Url" not in response.headers
    assert not tmdb_mock.calls.called


async def test_unknown_category_is_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.get("/swipe/deck", params={"types": "cartoons"}, headers=HTMX)

    assert response.status_code == 400


async def test_deck_shows_message_when_tmdb_is_down(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await login(client, db_session)
    mock_genres(tmdb_mock)
    tmdb_mock.get("/discover/tv").mock(return_value=httpx.Response(404))

    response = await client.get("/swipe/deck", params={"types": "series"}, headers=HTMX)

    assert response.status_code == 200
    assert "Не вдалося завантажити картки" in response.text


async def test_liked_title_goes_to_separate_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login(client, db_session)
    headers = await csrf_headers(client)

    liked = await client.post("/swipe/decisions", data=decision(1), headers=headers)
    skipped = await client.post("/swipe/decisions", data=decision(2, liked=False), headers=headers)

    assert liked.json() == {"liked_count": 1}
    assert skipped.json() == {"liked_count": 1}
    page_html = (await client.get("/swipe/liked")).text
    assert "Тайтл 1" in page_html
    assert 'href="/tmdb/tv/1"' in page_html
    assert "Тайтл 2" not in page_html
    assert "Тайтл 1" not in (await client.get("/list")).text


async def test_repeated_decision_updates_existing_swipe(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login(client, db_session)
    headers = await csrf_headers(client)

    await client.post("/swipe/decisions", data=decision(1, liked=False), headers=headers)
    response = await client.post("/swipe/decisions", data=decision(1), headers=headers)

    assert response.json() == {"liked_count": 1}


async def test_decision_requires_csrf(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.post("/swipe/decisions", data=decision(1))

    assert response.status_code == 403


async def test_decision_rejects_foreign_poster_path(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login(client, db_session)

    response = await client.post(
        "/swipe/decisions",
        data=decision(1, poster_path="/../../evil"),
        headers=await csrf_headers(client),
    )

    assert response.status_code == 400


async def test_remove_from_liked(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)
    headers = await csrf_headers(client)
    await client.post("/swipe/decisions", data=decision(1), headers=headers)
    await client.post("/swipe/decisions", data=decision(2), headers=headers)

    first = await client.delete("/swipe/liked/tv/1", headers=headers)
    second = await client.delete("/swipe/liked/tv/2", headers=headers)

    assert first.text == ""
    assert "HX-Refresh" not in first.headers
    assert second.headers["HX-Refresh"] == "true"
    assert "Почати свайпати" in (await client.get("/swipe/liked")).text


async def test_liked_list_is_private(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session, "first")
    await client.post("/swipe/decisions", data=decision(1), headers=await csrf_headers(client))
    await client.post("/logout", data={"csrf_token": await fetch_csrf_token(client, "/")})
    await login(client, db_session, "second")

    assert "Тайтл 1" not in (await client.get("/swipe/liked")).text


async def test_discover_fills_missing_overview_from_fallback_language(
    tmdb_client: TMDBClient, tmdb_mock: respx.MockRouter
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["language"] == "en-US":
            return page(tv(1, "Name", overview="English overview"))
        return page(tv(1, "Назва", overview=""))

    tmdb_mock.get("/discover/tv").mock(side_effect=respond)

    filled = await tmdb_client.discover("tv", {}, fill_overviews=True)
    untouched = await tmdb_client.discover("tv", {})

    assert filled[0].name == "Назва"
    assert filled[0].overview == "English overview"
    assert untouched[0].overview == ""
