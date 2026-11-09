from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MediaType = Literal["tv", "movie"]


class TMDBModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


def blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Genre(TMDBModel):
    id: int
    name: str


class SearchResult(TMDBModel):
    id: int
    media_type: Literal["tv", "movie"]
    name: str
    original_name: str | None = None
    overview: str = ""
    poster_path: str | None = None
    release_date: date | None = None

    @field_validator("release_date", "poster_path", "original_name", mode="before")
    @classmethod
    def normalize_blank(cls, value: object) -> object:
        return blank_to_none(value)


class BrowseItem(SearchResult):
    backdrop_path: str | None = None
    popularity: float = 0
    vote_count: int = 0
    genre_ids: list[int] = Field(default_factory=list)
    original_language: str = ""
    adult: bool = False


class SearchPage(TMDBModel):
    page: int = 1
    total_pages: int = 0
    total_results: int = 0
    results: list[SearchResult] = Field(default_factory=list)


class EpisodeDetails(TMDBModel):
    episode_number: int
    name: str = ""
    air_date: date | None = None
    runtime: int | None = None

    @field_validator("air_date", mode="before")
    @classmethod
    def normalize_blank(cls, value: object) -> object:
        return blank_to_none(value)


class SeasonSummary(TMDBModel):
    season_number: int
    name: str = ""
    air_date: date | None = None
    episode_count: int = 0

    @field_validator("air_date", mode="before")
    @classmethod
    def normalize_blank(cls, value: object) -> object:
        return blank_to_none(value)


class SeasonDetails(SeasonSummary):
    episodes: list[EpisodeDetails] = Field(default_factory=list)


class TitleDetails(TMDBModel):
    id: int
    name: str
    original_name: str | None = None
    overview: str = ""
    poster_path: str | None = None
    release_date: date | None = None
    status: str | None = None
    genres: list[Genre] = Field(default_factory=list)

    @field_validator("release_date", "poster_path", "original_name", "status", mode="before")
    @classmethod
    def normalize_blank(cls, value: object) -> object:
        return blank_to_none(value)


class MovieDetails(TitleDetails):
    runtime: int | None = None


class SeriesDetails(TitleDetails):
    seasons: list[SeasonSummary] = Field(default_factory=list)


class SeriesWithEpisodes(TMDBModel):
    details: SeriesDetails
    seasons: list[SeasonDetails]
