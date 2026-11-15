import httpx
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, register_user

HTMX = {"HX-Request": "true"}


def credits(*cast: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"id": 1, "cast": list(cast)})


async def test_series_cast_uses_aggregate_credits(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)
    route = tmdb_mock.get("/tv/5/aggregate_credits").mock(
        return_value=credits(
            {
                "id": 2,
                "name": "Друга роль",
                "order": 1,
                "profile_path": None,
                "roles": [{"character": "Епізод", "episode_count": 1}],
            },
            {
                "id": 1,
                "name": "Головний актор",
                "order": 0,
                "profile_path": "/face.jpg",
                "roles": [
                    {"character": "", "episode_count": 30},
                    {"character": "Патрік Джейн", "episode_count": 151},
                ],
            },
        )
    )

    response = await client.get(f"/titles/{title_id}/cast", headers=HTMX)

    assert "Актори" in response.text
    assert response.text.index("Головний актор") < response.text.index("Друга роль")
    assert "Патрік Джейн" in response.text
    assert "w185/face.jpg" in response.text
    assert 'href="https://www.themoviedb.org/person/1"' in response.text
    assert route.called


async def test_movie_cast_uses_credits_and_is_cached(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=7, title_type="movie")
    route = tmdb_mock.get("/movie/7/credits").mock(
        return_value=credits({"id": 3, "name": "Кіноактор", "character": "Кобб", "order": 0})
    )

    first = await client.get(f"/titles/{title_id}/cast", headers=HTMX)
    await client.get(f"/titles/{title_id}/cast", headers=HTMX)

    assert "Кобб" in first.text
    assert route.call_count == 1


async def test_cast_is_hidden_when_empty_or_failing(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    empty_id = await create_title(db_session, tmdb_id=5)
    broken_id = await create_title(db_session, tmdb_id=6)
    tmdb_mock.get("/tv/5/aggregate_credits").mock(return_value=credits())
    tmdb_mock.get("/tv/6/aggregate_credits").mock(return_value=httpx.Response(404))

    empty = await client.get(f"/titles/{empty_id}/cast", headers=HTMX)
    broken = await client.get(f"/titles/{broken_id}/cast", headers=HTMX)

    assert empty.text == ""
    assert broken.text == ""


async def test_title_page_loads_cast_lazily(client: AsyncClient, db_session: AsyncSession) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)

    response = await client.get(f"/titles/{title_id}")

    assert f'hx-get="/titles/{title_id}/cast"' in response.text


async def test_cast_requires_login(client: AsyncClient) -> None:
    response = await client.get("/titles/1/cast", headers=HTMX)

    assert response.headers["HX-Redirect"].startswith("/login")
