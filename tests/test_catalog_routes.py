import httpx
import respx
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests import tmdb_fakes
from tests.conftest import register_user
from tests.test_catalog_service import mock_series
from watchlist.catalog.models import Title


async def test_search_requires_login(client: AsyncClient) -> None:
    response = await client.get("/search?q=test")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


async def test_search_page_without_query(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.get("/search")

    assert response.status_code == 200
    assert 'name="q"' in response.text


async def test_search_shows_results(client: AsyncClient, tmdb_mock: respx.MockRouter) -> None:
    await register_user(client)
    tmdb_mock.get("/search/multi").respond(
        json=tmdb_fakes.search_page(tmdb_fakes.search_tv(), tmdb_fakes.search_movie())
    )

    response = await client.get("/search", params={"q": "гра"})

    assert response.status_code == 200
    assert "Гра престолів" in response.text
    assert 'href="/tmdb/tv/1399"' in response.text
    assert 'href="/tmdb/movie/27205"' in response.text
    assert "<html" in response.text


async def test_htmx_search_returns_fragment(
    client: AsyncClient, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    tmdb_mock.get("/search/multi").respond(json=tmdb_fakes.search_page(tmdb_fakes.search_tv()))

    response = await client.get("/search", params={"q": "гра"}, headers={"HX-Request": "true"})

    assert "Гра престолів" in response.text
    assert "<html" not in response.text


async def test_search_without_results(client: AsyncClient, tmdb_mock: respx.MockRouter) -> None:
    await register_user(client)
    tmdb_mock.get("/search/multi").respond(json=tmdb_fakes.search_page())

    response = await client.get("/search", params={"q": "zzzz"})

    assert "нічого не знайдено" in response.text


async def test_search_when_tmdb_is_down(client: AsyncClient, tmdb_mock: respx.MockRouter) -> None:
    await register_user(client)
    tmdb_mock.get("/search/multi").respond(503)

    response = await client.get("/search", params={"q": "гра"})

    assert response.status_code == 200
    assert "тимчасово недоступний" in response.text


async def test_open_tmdb_title_imports_and_redirects(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    mock_series(tmdb_mock)

    response = await client.get("/tmdb/tv/1399")

    title_id = await db_session.scalar(select(Title.id).where(Title.tmdb_id == 1399))
    assert response.status_code == 303
    assert response.headers["location"] == f"/titles/{title_id}"

    page = await client.get(response.headers["location"])
    assert page.status_code == 200
    assert "Гра престолів" in page.text
    assert "Game of Thrones" in page.text
    assert "Сезон 2" in page.text
    assert "Епізод 3" in page.text


async def test_reopening_title_does_not_duplicate(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    route = mock_series(tmdb_mock)

    first = await client.get("/tmdb/tv/1399")
    calls = route.call_count
    second = await client.get("/tmdb/tv/1399")

    assert first.headers["location"] == second.headers["location"]
    assert route.call_count == calls
    assert await db_session.scalar(select(func.count()).select_from(Title)) == 1


async def test_open_movie(client: AsyncClient, tmdb_mock: respx.MockRouter) -> None:
    await register_user(client)
    tmdb_mock.get("/movie/27205").respond(json=tmdb_fakes.movie())

    response = await client.get("/tmdb/movie/27205", follow_redirects=True)

    assert response.status_code == 200
    assert "Початок" in response.text
    assert "Сезони" not in response.text


async def test_open_unknown_tmdb_title(client: AsyncClient, tmdb_mock: respx.MockRouter) -> None:
    await register_user(client)
    tmdb_mock.get("/tv/999").respond(404)

    response = await client.get("/tmdb/tv/999")

    assert response.status_code == 404
    assert "Не знайдено" in response.text


async def test_open_title_when_tmdb_is_down(
    client: AsyncClient, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    tmdb_mock.get("/movie/1").mock(side_effect=httpx.ConnectError("down"))

    response = await client.get("/tmdb/movie/1")

    assert response.status_code == 502
    assert "TMDB недоступний" in response.text


async def test_invalid_media_type(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.get("/tmdb/person/1")

    assert response.status_code == 400


async def test_missing_title_page(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.get("/titles/999999")

    assert response.status_code == 404


async def test_unknown_route_renders_html_404(client: AsyncClient) -> None:
    response = await client.get("/no-such-page")

    assert response.status_code == 404
    assert "Такої сторінки не існує" in response.text
