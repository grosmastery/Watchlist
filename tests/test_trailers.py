from typing import Any

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, register_user
from watchlist.catalog.trailers import pick_trailer
from watchlist.integrations.tmdb import TMDBClient
from watchlist.integrations.tmdb_schemas import Video

HTMX = {"HX-Request": "true"}


def video(key: str, **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "site": "YouTube",
        "type": "Trailer",
        "official": True,
        "iso_639_1": "en",
        **extra,
    }


def videos(*items: dict[str, Any]) -> list[Video]:
    return [Video.model_validate(item) for item in items]


def test_pick_trailer_prefers_ukrainian_then_trailer_then_official() -> None:
    choices = videos(
        video("enTrailer1"),
        video("ukTeaser01", type="Teaser", iso_639_1="uk"),
        video("ukUnofficial", official=False, iso_639_1="uk"),
        video("ukOfficial1", iso_639_1="uk"),
    )

    assert pick_trailer(choices, "uk") == "ukOfficial1"


def test_pick_trailer_falls_back_to_english() -> None:
    choices = videos(video("enTeaser01", type="Teaser"), video("enTrailer1"))

    assert pick_trailer(choices, "uk") == "enTrailer1"


def test_pick_trailer_ignores_other_sites_types_and_bad_keys() -> None:
    choices = videos(
        video("vimeo12345", site="Vimeo"),
        video("clip123456", type="Clip"),
        video('bad key"><'),
    )

    assert pick_trailer(choices, "uk") is None


async def test_client_requests_ukrainian_and_english_videos(
    tmdb_client: TMDBClient, tmdb_mock: respx.MockRouter
) -> None:
    route = tmdb_mock.get("/tv/5/videos").mock(
        return_value=httpx.Response(200, json={"results": [video("abcdef1234")]})
    )

    result = await tmdb_client.get_videos("tv", 5)

    assert [item.key for item in result] == ["abcdef1234"]
    assert route.calls.last.request.url.params["include_video_language"] == "uk,en,null"


async def test_trailer_button_embeds_youtube_video(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, name="Серіал", tmdb_id=5)
    tmdb_mock.get("/tv/5/videos").mock(
        return_value=httpx.Response(200, json={"results": [video("abcdef1234")]})
    )

    response = await client.get(f"/titles/{title_id}/trailer", headers=HTMX)

    assert "Трейлер" in response.text
    assert "youtube-nocookie.com/embed/abcdef1234" in response.text


async def test_trailer_is_cached(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, name="Фільм", tmdb_id=7, title_type="movie")
    route = tmdb_mock.get("/movie/7/videos").mock(
        return_value=httpx.Response(200, json={"results": [video("abcdef1234")]})
    )

    await client.get(f"/titles/{title_id}/trailer", headers=HTMX)
    await client.get(f"/titles/{title_id}/trailer", headers=HTMX)

    assert route.call_count == 1


async def test_no_button_without_trailer(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)
    tmdb_mock.get("/tv/5/videos").mock(return_value=httpx.Response(200, json={"results": []}))

    response = await client.get(f"/titles/{title_id}/trailer", headers=HTMX)

    assert response.status_code == 200
    assert response.text == ""


async def test_no_button_when_tmdb_fails(
    client: AsyncClient, db_session: AsyncSession, tmdb_mock: respx.MockRouter
) -> None:
    await register_user(client)
    title_id = await create_title(db_session, tmdb_id=5)
    tmdb_mock.get("/tv/5/videos").mock(return_value=httpx.Response(401))

    response = await client.get(f"/titles/{title_id}/trailer", headers=HTMX)

    assert response.status_code == 200
    assert response.text == ""


async def test_title_page_requests_trailer_lazily(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await register_user(client)
    title_id = await create_title(db_session)

    response = await client.get(f"/titles/{title_id}")

    assert f'hx-get="/titles/{title_id}/trailer"' in response.text


async def test_trailer_requires_login(client: AsyncClient, db_session: AsyncSession) -> None:
    title_id = await create_title(db_session)

    response = await client.get(f"/titles/{title_id}/trailer", headers=HTMX)

    assert response.headers["HX-Redirect"].startswith("/login")
