from typing import Any

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, register_user

HTMX = {"HX-Request": "true"}


def tv(tmdb_id: int, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "name": name,
        "original_name": name,
        "original_language": "uk",
        "media_type": "tv",
        "first_air_date": "2019-01-01",
        "poster_path": f"/{tmdb_id}.jpg",
        "genre_ids": [18],
        **extra,
    }


def page(*results: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"page": 1, "total_pages": 1, "results": list(results)})


async def test_similar_shows_recommendations(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)
    tmdb_mock.get("/tv/5/recommendations").mock(
        return_value=page(
            tv(6, "Рекомендований"), tv(7, "Без постера", poster_path=None), tv(5, "Сам тайтл")
        )
    )
    similar = tmdb_mock.get("/tv/5/similar").mock(return_value=page(tv(8, "Схожий")))

    response = await client.get(f"/titles/{title_id}/similar", headers=HTMX)

    assert "Схожі" in response.text
    assert "Рекомендований" in response.text
    assert 'href="/tmdb/tv/6"' in response.text
    assert "Без постера" not in response.text
    assert "Сам тайтл" not in response.text
    assert not similar.called


async def test_similar_falls_back_to_similar_endpoint(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)
    tmdb_mock.get("/tv/5/recommendations").mock(return_value=page())
    tmdb_mock.get("/tv/5/similar").mock(return_value=page(tv(8, "Схожий за жанром")))

    response = await client.get(f"/titles/{title_id}/similar", headers=HTMX)

    assert "Схожий за жанром" in response.text


async def test_similar_is_hidden_when_nothing_found(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)
    tmdb_mock.get("/tv/5/recommendations").mock(return_value=page())
    tmdb_mock.get("/tv/5/similar").mock(return_value=page())

    response = await client.get(f"/titles/{title_id}/similar", headers=HTMX)

    assert response.text == ""


async def test_similar_is_hidden_when_tmdb_fails(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)
    tmdb_mock.get("/tv/5/recommendations").mock(return_value=httpx.Response(401))

    response = await client.get(f"/titles/{title_id}/similar", headers=HTMX)

    assert response.status_code == 200
    assert response.text == ""


async def test_similar_is_cached(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=7, title_type="movie")
    route = tmdb_mock.get("/movie/7/recommendations").mock(
        return_value=page(tv(6, "Фільм", media_type="movie"))
    )

    await client.get(f"/titles/{title_id}/similar", headers=HTMX)
    await client.get(f"/titles/{title_id}/similar", headers=HTMX)

    assert route.call_count == 1


async def test_title_page_loads_similar_when_scrolled_to(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await register_user(client)
    title_id = await create_title(db_session)

    response = await client.get(f"/titles/{title_id}")

    assert f'hx-get="/titles/{title_id}/similar" hx-trigger="revealed"' in response.text
