import asyncio
import enum
import random
from collections.abc import Callable, Collection
from dataclasses import dataclass
from itertools import zip_longest

from watchlist.home.feeds import (
    ANIME_FILTER,
    SERIES_FILTER,
    default_subtitle,
    is_anime,
    is_listable,
    is_regular_series,
)
from watchlist.home.recommendations import GenreNames, TitleKey, item_genres
from watchlist.integrations.tmdb import TMDBClient
from watchlist.integrations.tmdb_schemas import BrowseItem, MediaType

DECK_SIZE = 12
MAX_ROUNDS = 4
MOVIE_PAGES = 500
SERIES_PAGES = 200
ANIME_SERIES_PAGES = 70
ANIME_MOVIE_PAGES = 40
MOVIE_MIN_VOTES = 300
SERIES_MIN_VOTES = 100
ANIME_MIN_VOTES = 30
RANDOM = random.SystemRandom()


class SwipeCategory(enum.StrEnum):
    SERIES = "series"
    MOVIES = "movies"
    ANIME = "anime"

    @property
    def label(self) -> str:
        return CATEGORY_LABELS[self]


CATEGORY_LABELS = {
    SwipeCategory.SERIES: "Серіали",
    SwipeCategory.MOVIES: "Фільми",
    SwipeCategory.ANIME: "Аніме",
}


@dataclass(frozen=True, slots=True)
class Source:
    media_type: MediaType
    params: dict[str, str | int]
    pages: int
    keep: Callable[[BrowseItem], bool]


@dataclass(frozen=True, slots=True)
class SwipeCard:
    media_type: MediaType
    tmdb_id: int
    name: str
    poster_path: str | None
    subtitle: str
    overview: str
    rating: float
    genres: tuple[str, ...]

    @property
    def url(self) -> str:
        return f"/tmdb/{self.media_type}/{self.tmdb_id}"


def is_regular_movie(item: BrowseItem) -> bool:
    return not is_anime(item)


SOURCES: dict[SwipeCategory, tuple[Source, ...]] = {
    SwipeCategory.SERIES: (
        Source(
            "tv",
            {**SERIES_FILTER, "vote_count.gte": SERIES_MIN_VOTES},
            SERIES_PAGES,
            is_regular_series,
        ),
    ),
    SwipeCategory.MOVIES: (
        Source("movie", {"vote_count.gte": MOVIE_MIN_VOTES}, MOVIE_PAGES, is_regular_movie),
    ),
    SwipeCategory.ANIME: (
        Source(
            "tv", {**ANIME_FILTER, "vote_count.gte": ANIME_MIN_VOTES}, ANIME_SERIES_PAGES, is_anime
        ),
        Source(
            "movie",
            {**ANIME_FILTER, "vote_count.gte": ANIME_MIN_VOTES},
            ANIME_MOVIE_PAGES,
            is_anime,
        ),
    ),
}


def to_swipe_card(item: BrowseItem, genres: GenreNames) -> SwipeCard:
    return SwipeCard(
        media_type=item.media_type,
        tmdb_id=item.id,
        name=item.name,
        poster_path=item.poster_path,
        subtitle=default_subtitle(item),
        overview=item.overview,
        rating=item.vote_average,
        genres=tuple(item_genres(item, genres)),
    )


async def fetch_source(client: TMDBClient, source: Source) -> list[BrowseItem]:
    items = await client.discover(
        source.media_type,
        source.params,
        page=RANDOM.randint(1, source.pages),
        fill_overviews=True,
    )
    kept = [item for item in items if is_listable(item) and source.keep(item)]
    RANDOM.shuffle(kept)
    return kept


async def draw_deck(
    client: TMDBClient,
    categories: Collection[SwipeCategory],
    excluded: set[TitleKey],
    genres: GenreNames,
) -> list[SwipeCard]:
    drawn: dict[TitleKey, BrowseItem] = {}
    for _ in range(MAX_ROUNDS):
        batches = await asyncio.gather(
            *(fetch_source(client, RANDOM.choice(SOURCES[category])) for category in categories)
        )
        for group in zip_longest(*batches):
            for item in group:
                if item is not None and (item.media_type, item.id) not in excluded:
                    drawn.setdefault((item.media_type, item.id), item)
        if len(drawn) >= DECK_SIZE:
            break
    items = list(drawn.values())[:DECK_SIZE]
    RANDOM.shuffle(items)
    return [to_swipe_card(item, genres) for item in items]
