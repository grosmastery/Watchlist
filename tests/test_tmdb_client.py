from datetime import date

import httpx
import pytest
import respx

from tests import tmdb_fakes
from watchlist.integrations.tmdb import (
    TMDBClient,
    TMDBNotConfiguredError,
    TMDBNotFoundError,
    TMDBUnavailableError,
)


async def test_search_keeps_only_series_and_movies(
    tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    route = tmdb_mock.get("/search/multi").respond(
        json=tmdb_fakes.search_page(
            tmdb_fakes.search_tv(), tmdb_fakes.search_person(), tmdb_fakes.search_movie()
        )
    )

    page = await tmdb_client.search("гра")

    assert [item.media_type for item in page.results] == ["tv", "movie"]
    movie = page.results[1]
    assert movie.name == "Початок"
    assert movie.original_name == "Inception"
    assert movie.release_date is None
    assert page.results[0].release_date == date(2011, 4, 17)
    request = route.calls.last.request
    assert request.url.params["query"] == "гра"
    assert request.url.params["language"] == "uk-UA"
    assert request.url.params["api_key"] == "test-key"


async def test_bearer_token_is_sent_as_header(tmdb_mock: respx.MockRouter) -> None:
    route = tmdb_mock.get("/search/multi").respond(json=tmdb_fakes.search_page())

    async with TMDBClient("eyJhbGciOi.test.token") as client:
        await client.search("x")

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer eyJhbGciOi.test.token"
    assert "api_key" not in request.url.params


async def test_missing_key_raises_not_configured() -> None:
    async with TMDBClient(None) as client:
        with pytest.raises(TMDBNotConfiguredError):
            await client.search("x")


async def test_retries_on_rate_limit(tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient) -> None:
    route = tmdb_mock.get("/movie/27205")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(503),
        httpx.Response(200, json=tmdb_fakes.movie()),
    ]

    movie = await tmdb_client.get_movie(27205)

    assert movie.name == "Початок"
    assert route.call_count == 3


async def test_gives_up_after_retries(tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient) -> None:
    route = tmdb_mock.get("/movie/1").respond(500)

    with pytest.raises(TMDBUnavailableError):
        await tmdb_client.get_movie(1)

    assert route.call_count == tmdb_client.max_retries + 1


async def test_network_errors_are_retried(
    tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    route = tmdb_mock.get("/movie/27205")
    route.side_effect = [
        httpx.ConnectTimeout("timeout"),
        httpx.Response(200, json=tmdb_fakes.movie()),
    ]

    movie = await tmdb_client.get_movie(27205)

    assert movie.id == 27205


async def test_not_found(tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient) -> None:
    tmdb_mock.get("/tv/999").respond(404, json={"status_code": 34})

    with pytest.raises(TMDBNotFoundError):
        await tmdb_client.get_series(999)


async def test_empty_overview_falls_back_to_english(
    tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    route = tmdb_mock.get("/movie/27205")
    route.side_effect = [
        httpx.Response(200, json=tmdb_fakes.movie(overview="")),
        httpx.Response(200, json=tmdb_fakes.movie(overview="English overview")),
    ]

    movie = await tmdb_client.get_movie(27205)

    assert movie.overview == "English overview"
    assert movie.name == "Початок"
    assert route.calls[1].request.url.params["language"] == "en-US"


async def test_series_with_episodes_batches_seasons_and_skips_specials(
    tmdb_mock: respx.MockRouter, tmdb_client: TMDBClient
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        append = request.url.params.get("append_to_response")
        if not append:
            return httpx.Response(200, json=tmdb_fakes.series(1399, seasons=25))
        numbers = [int(part.split("/")[1]) for part in append.split(",")]
        return httpx.Response(200, json=tmdb_fakes.series_with_seasons(1399, numbers))

    route = tmdb_mock.get("/tv/1399").mock(side_effect=respond)

    data = await tmdb_client.get_series_with_episodes(1399)

    assert [season.season_number for season in data.seasons] == list(range(1, 26))
    assert all(len(season.episodes) == 3 for season in data.seasons)
    assert route.call_count == 3
    appended = [call.request.url.params.get("append_to_response") for call in route.calls[1:]]
    assert all(len(value.split(",")) <= 20 for value in appended)
    assert not any("season/0" in value.split(",") for value in appended)
