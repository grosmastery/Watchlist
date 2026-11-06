import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Self

import httpx

from watchlist.integrations.tmdb_schemas import (
    MovieDetails,
    SearchPage,
    SeasonDetails,
    SeriesDetails,
    SeriesWithEpisodes,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"
APPEND_LIMIT = 20
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
FIELD_ALIASES = {
    "title": "name",
    "original_title": "original_name",
    "first_air_date": "release_date",
}


class TMDBError(Exception):
    pass


class TMDBNotConfiguredError(TMDBError):
    pass


class TMDBNotFoundError(TMDBError):
    pass


class TMDBUnavailableError(TMDBError):
    pass


def normalize(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    for source, target in FIELD_ALIASES.items():
        if source in result and target not in result:
            result[target] = result.pop(source)
    return result


def chunked(items: Sequence[int], size: int) -> list[Sequence[int]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


class TMDBClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        language: str = "uk-UA",
        fallback_language: str = "en-US",
        base_url: str = BASE_URL,
        max_retries: int = 3,
        backoff: float = 0.5,
        max_concurrency: int = 8,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.language = language
        self.fallback_language = fallback_language
        self.max_retries = max_retries
        self.backoff = backoff
        self._semaphore = asyncio.Semaphore(max_concurrency)
        headers = {"Accept": "application/json"}
        params: dict[str, str] = {}
        if api_key and api_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {api_key}"
        elif api_key:
            params["api_key"] = api_key
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            params=params,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        if not self.api_key:
            raise TMDBNotConfiguredError("TMDB_API_KEY is not set")
        query = {"language": self.language, **(params or {})}
        for attempt in range(self.max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._http.get(path, params=query)
            except httpx.TransportError as error:
                if attempt == self.max_retries:
                    raise TMDBUnavailableError(str(error)) from error
                await self._sleep(attempt, None)
                continue
            if response.status_code == 404:
                raise TMDBNotFoundError(path)
            if response.status_code in RETRY_STATUSES:
                if attempt == self.max_retries:
                    raise TMDBUnavailableError(f"{path}: HTTP {response.status_code}")
                await self._sleep(attempt, response.headers.get("Retry-After"))
                continue
            if response.is_error:
                raise TMDBError(f"{path}: HTTP {response.status_code}")
            return response.json()
        raise TMDBUnavailableError(path)

    async def _sleep(self, attempt: int, retry_after: str | None) -> None:
        delay = self.backoff * 2**attempt
        if retry_after and retry_after.isdigit():
            delay = max(delay, float(retry_after))
        logger.warning("TMDB request failed, retrying in %.1fs", delay)
        await asyncio.sleep(delay)

    async def _with_fallback_overview(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("overview"):
            return payload
        fallback = await self._get(path, {"language": self.fallback_language})
        payload["overview"] = fallback.get("overview") or ""
        if not payload.get("poster_path"):
            payload["poster_path"] = fallback.get("poster_path")
        return payload

    async def search(self, query: str, page: int = 1) -> SearchPage:
        payload = await self._get(
            "/search/multi", {"query": query, "page": page, "include_adult": "false"}
        )
        results = [
            normalize(item)
            for item in payload.get("results", [])
            if item.get("media_type") in {"tv", "movie"}
        ]
        return SearchPage.model_validate({**payload, "results": results})

    async def get_movie(self, tmdb_id: int) -> MovieDetails:
        path = f"/movie/{tmdb_id}"
        payload = await self._with_fallback_overview(path, await self._get(path))
        return MovieDetails.model_validate(normalize(payload))

    async def get_series(self, tmdb_id: int) -> SeriesDetails:
        path = f"/tv/{tmdb_id}"
        payload = await self._with_fallback_overview(path, await self._get(path))
        return SeriesDetails.model_validate(normalize(payload))

    async def get_season(self, tmdb_id: int, season_number: int) -> SeasonDetails:
        payload = await self._get(f"/tv/{tmdb_id}/season/{season_number}")
        return SeasonDetails.model_validate(payload)

    async def _get_seasons(self, tmdb_id: int, numbers: Sequence[int]) -> list[SeasonDetails]:
        payload = await self._get(
            f"/tv/{tmdb_id}",
            {"append_to_response": ",".join(f"season/{number}" for number in numbers)},
        )
        return [
            SeasonDetails.model_validate(payload[f"season/{number}"])
            for number in numbers
            if payload.get(f"season/{number}")
        ]

    async def get_series_with_episodes(self, tmdb_id: int) -> SeriesWithEpisodes:
        details = await self.get_series(tmdb_id)
        numbers = sorted(
            season.season_number for season in details.seasons if season.season_number > 0
        )
        batches = await asyncio.gather(
            *(self._get_seasons(tmdb_id, batch) for batch in chunked(numbers, APPEND_LIMIT))
        )
        seasons = [season for batch in batches for season in batch]
        return SeriesWithEpisodes(details=details, seasons=seasons)
