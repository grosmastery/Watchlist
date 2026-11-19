from typing import Any

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, register_user
from watchlist.integrations.tmdb import TMDBClient

HTMX = {"HX-Request": "true"}
LINK = "https://www.themoviedb.org/movie/7-film/watch?locale=UA"


def provider(provider_id: int, name: str, priority: int = 0, **extra: Any) -> dict[str, Any]:
    return {
        "provider_id": provider_id,
        "provider_name": name,
        "logo_path": f"/{provider_id}.png",
        "display_priority": priority,
        **extra,
    }


def providers(**regions: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"id": 7, "results": regions})


async def test_watch_groups_providers_for_ukraine(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=7, title_type="movie")
    tmdb_mock.get("/movie/7/watch/providers").mock(
        return_value=providers(
            UA={
                "link": LINK,
                "flatrate": [provider(2, "Другий", 5), provider(1, "Перший", 1)],
                "rent": [provider(3, "Прокат")],
                "buy": [provider(3, "Прокат")],
                "free": [provider(4, "Безкоштовний")],
                "ads": [provider(4, "Безкоштовний")],
            },
            US={"link": "x", "flatrate": [provider(9, "Американський")]},
        )
    )

    response = await client.get(f"/titles/{title_id}/watch", headers=HTMX)

    text = response.text
    assert "Де дивитись" in text
    assert text.index("Підписка") < text.index("Безкоштовно") < text.index("Оренда")
    assert text.index("Оренда") < text.index("Купівля")
    assert text.index('alt="Перший"') < text.index('alt="Другий"')
    assert text.count('alt="Безкоштовний"') == 1
    assert "Американський" not in text
    assert f'href="{LINK}"' in text
    assert "w92/1.png" in text
    assert "JustWatch" in text


async def test_watch_is_cached(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)
    route = tmdb_mock.get("/tv/5/watch/providers").mock(
        return_value=providers(UA={"link": LINK, "flatrate": [provider(1, "Сервіс")]})
    )

    await client.get(f"/titles/{title_id}/watch", headers=HTMX)
    await client.get(f"/titles/{title_id}/watch", headers=HTMX)

    assert route.call_count == 1


async def test_watch_replaces_foreign_link(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)
    tmdb_mock.get("/tv/5/watch/providers").mock(
        return_value=providers(
            UA={"link": "javascript:alert(1)", "flatrate": [provider(1, "Сервіс", logo_path=None)]}
        )
    )

    response = await client.get(f"/titles/{title_id}/watch", headers=HTMX)

    assert "javascript:" not in response.text
    assert 'href="https://www.themoviedb.org/tv/5/watch"' in response.text
    assert "Сервіс" in response.text


async def test_watch_is_hidden_without_providers_or_on_failure(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    other_region = await create_title(db_session, tmdb_id=5)
    only_link = await create_title(db_session, tmdb_id=6)
    broken = await create_title(db_session, tmdb_id=8)
    tmdb_mock.get("/tv/5/watch/providers").mock(
        return_value=providers(US={"flatrate": [provider(1, "Сервіс")]})
    )
    tmdb_mock.get("/tv/6/watch/providers").mock(return_value=providers(UA={"link": LINK}))
    tmdb_mock.get("/tv/8/watch/providers").mock(return_value=httpx.Response(404))

    for title_id in (other_region, only_link, broken):
        response = await client.get(f"/titles/{title_id}/watch", headers=HTMX)
        assert response.status_code == 200
        assert response.text == ""


async def test_title_page_loads_watch_lazily(client: AsyncClient, db_session: AsyncSession) -> None:
    await register_user(client)
    title_id = await create_title(db_session)

    response = await client.get(f"/titles/{title_id}")

    assert f'hx-get="/titles/{title_id}/watch"' in response.text


async def test_watch_requires_login(client: AsyncClient, db_session: AsyncSession) -> None:
    title_id = await create_title(db_session)

    response = await client.get(f"/titles/{title_id}/watch", headers=HTMX)

    assert "HX-Redirect" in response.headers


def test_region_follows_language_unless_given() -> None:
    assert TMDBClient(None).region == "UA"
    assert TMDBClient(None, language="en-US").region == "US"
    assert TMDBClient(None, region="pl").region == "PL"
