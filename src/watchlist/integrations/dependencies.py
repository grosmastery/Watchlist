from typing import Annotated

from fastapi import Depends, Request

from watchlist.config import Settings
from watchlist.integrations.tmdb import TMDBClient


def build_tmdb_client(settings: Settings) -> TMDBClient:
    api_key = settings.tmdb_api_key.get_secret_value() if settings.tmdb_api_key else None
    return TMDBClient(
        api_key,
        language=settings.tmdb_language,
        fallback_language=settings.tmdb_fallback_language,
    )


def get_tmdb_client(request: Request) -> TMDBClient:
    client: TMDBClient = request.app.state.tmdb_client
    return client


TMDB = Annotated[TMDBClient, Depends(get_tmdb_client)]
