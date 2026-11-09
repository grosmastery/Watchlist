import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import zip_longest
from typing import Literal

from watchlist.integrations.tmdb import TMDBClient, TMDBError
from watchlist.integrations.tmdb_schemas import BrowseItem, MediaType, SeasonSummary

FeedTab = Literal["series", "movies", "anime"]

FEED_SIZE = 20
NEW_WINDOW = timedelta(days=90)
SEASON_PAGES = 2
TRENDING_PAGES = 2
HERO_POOL = 6
ANIMATION_GENRE = 16
ANIME_LANGUAGE = "ja"
NON_FICTION_GENRES = frozenset({10763, 10764, 10766, 10767})
ANIME_FILTER = {"with_genres": str(ANIMATION_GENRE), "with_original_language": ANIME_LANGUAGE}
SERIES_FILTER = {"without_genres": ",".join(str(genre) for genre in sorted(NON_FICTION_GENRES))}
TYPE_LABELS: dict[MediaType, str] = {"tv": "Серіал", "movie": "Фільм"}
BADGE_PREMIERE = "Прем'єра"
BADGE_NEW_SEASON = "Новий сезон"


@dataclass(frozen=True, slots=True)
class Card:
    media_type: MediaType
    tmdb_id: int
    name: str
    poster_path: str | None
    subtitle: str
    badge: str | None = None
    note: str | None = None

    @property
    def key(self) -> tuple[MediaType, int]:
        return (self.media_type, self.tmdb_id)

    @property
    def url(self) -> str:
        return f"/tmdb/{self.media_type}/{self.tmdb_id}"

    @property
    def is_new_season(self) -> bool:
        return self.badge == BADGE_NEW_SEASON


@dataclass(frozen=True, slots=True)
class Hero:
    url: str
    name: str
    subtitle: str
    overview: str
    backdrop_path: str


def default_subtitle(item: BrowseItem) -> str:
    label = TYPE_LABELS[item.media_type]
    return f"{label} · {item.release_date.year}" if item.release_date else label


def is_listable(item: BrowseItem) -> bool:
    return bool(item.poster_path) and not item.adult


def is_anime(item: BrowseItem) -> bool:
    return ANIMATION_GENRE in item.genre_ids and item.original_language == ANIME_LANGUAGE


def is_regular_series(item: BrowseItem) -> bool:
    return not is_anime(item) and NON_FICTION_GENRES.isdisjoint(item.genre_ids)


def to_card(
    item: BrowseItem,
    *,
    badge: str | None = None,
    subtitle: str | None = None,
    note: str | None = None,
) -> Card:
    return Card(
        media_type=item.media_type,
        tmdb_id=item.id,
        name=item.name,
        poster_path=item.poster_path,
        subtitle=subtitle or default_subtitle(item),
        badge=badge,
        note=note,
    )


def interleave(*feeds: Sequence[Card], limit: int = FEED_SIZE) -> list[Card]:
    seen: set[tuple[MediaType, int]] = set()
    result: list[Card] = []
    for group in zip_longest(*feeds):
        for card in group:
            if card is None or card.key in seen:
                continue
            seen.add(card.key)
            result.append(card)
            if len(result) == limit:
                return result
    return result


def new_season_number(seasons: Iterable[SeasonSummary], start: date, today: date) -> int | None:
    numbers = [
        season.season_number
        for season in seasons
        if season.season_number > 1 and season.air_date and start <= season.air_date <= today
    ]
    return max(numbers, default=None)


def flatten[T](pages: Iterable[Iterable[T]]) -> list[T]:
    return [item for page in pages for item in page]


async def premieres(
    client: TMDBClient,
    params: dict[str, str | int],
    start: date,
    today: date,
    keep: Callable[[BrowseItem], bool],
) -> list[Card]:
    items = await client.discover(
        "tv",
        {
            **params,
            "first_air_date.gte": start.isoformat(),
            "first_air_date.lte": today.isoformat(),
        },
    )
    return [
        to_card(item, badge=BADGE_PREMIERE) for item in items if is_listable(item) and keep(item)
    ]


async def season_card(
    client: TMDBClient, item: BrowseItem, start: date, today: date
) -> Card | None:
    try:
        seasons = await client.get_season_summaries(item.id)
    except TMDBError:
        return None
    number = new_season_number(seasons, start, today)
    if number is None:
        return None
    return to_card(item, badge=BADGE_NEW_SEASON, subtitle=f"{number} сезон")


async def new_seasons(
    client: TMDBClient,
    params: dict[str, str | int],
    start: date,
    today: date,
    keep: Callable[[BrowseItem], bool],
) -> list[Card]:
    query = {**params, "air_date.gte": start.isoformat(), "air_date.lte": today.isoformat()}
    pages = await asyncio.gather(
        *(client.discover("tv", query, page=page) for page in range(1, SEASON_PAGES + 1))
    )
    candidates = [
        item
        for item in flatten(pages)
        if is_listable(item)
        and keep(item)
        and not (item.release_date and item.release_date >= start)
    ]
    cards = await asyncio.gather(*(season_card(client, item, start, today) for item in candidates))
    return [card for card in cards if card is not None]


async def new_series_feed(client: TMDBClient, today: date) -> list[Card]:
    start = today - NEW_WINDOW
    fresh, returning = await asyncio.gather(
        premieres(client, {**SERIES_FILTER, "vote_count.gte": 5}, start, today, is_regular_series),
        new_seasons(
            client, {**SERIES_FILTER, "vote_count.gte": 20}, start, today, is_regular_series
        ),
    )
    return interleave(fresh, returning)


async def new_movies_feed(client: TMDBClient, today: date) -> list[Card]:
    items = await client.discover(
        "movie",
        {
            "primary_release_date.gte": (today - NEW_WINDOW).isoformat(),
            "primary_release_date.lte": today.isoformat(),
            "vote_count.gte": 20,
        },
    )
    cards = [to_card(item) for item in items if is_listable(item) and not is_anime(item)]
    return cards[:FEED_SIZE]


async def new_anime_feed(client: TMDBClient, today: date) -> list[Card]:
    start = today - NEW_WINDOW
    tv_params: dict[str, str | int] = {**ANIME_FILTER, "vote_count.gte": 1}
    fresh, returning, movies = await asyncio.gather(
        premieres(client, tv_params, start, today, is_anime),
        new_seasons(client, tv_params, start, today, is_anime),
        client.discover(
            "movie",
            {
                **ANIME_FILTER,
                "primary_release_date.gte": start.isoformat(),
                "primary_release_date.lte": today.isoformat(),
                "vote_count.gte": 1,
            },
        ),
    )
    return interleave(fresh, returning, [to_card(item) for item in movies if is_listable(item)])


async def trending_cards(
    client: TMDBClient, media_type: MediaType, keep: Callable[[BrowseItem], bool]
) -> list[Card]:
    pages = await asyncio.gather(
        *(client.trending(media_type, page) for page in range(1, TRENDING_PAGES + 1))
    )
    items = [item for item in flatten(pages) if is_listable(item) and keep(item)]
    return interleave([to_card(item) for item in items])


async def popular_series_feed(client: TMDBClient, today: date) -> list[Card]:
    return await trending_cards(client, "tv", is_regular_series)


async def popular_movies_feed(client: TMDBClient, today: date) -> list[Card]:
    return await trending_cards(client, "movie", lambda item: not is_anime(item))


async def popular_anime_feed(client: TMDBClient, today: date) -> list[Card]:
    params: dict[str, str | int] = {**ANIME_FILTER, "vote_count.gte": 50}
    pages = await asyncio.gather(client.discover("tv", params), client.discover("movie", params))
    items = sorted(flatten(pages), key=lambda item: item.popularity, reverse=True)
    return interleave([to_card(item) for item in items if is_listable(item)])


async def hero_feed(client: TMDBClient, today: date) -> Hero | None:
    series, movies = await asyncio.gather(client.trending("tv"), client.trending("movie"))
    regular = [
        *(item for item in series if is_regular_series(item)),
        *(item for item in movies if not is_anime(item)),
    ]
    pool = sorted(
        (item for item in regular if is_listable(item) and item.backdrop_path and item.overview),
        key=lambda item: item.popularity,
        reverse=True,
    )[:HERO_POOL]
    if not pool:
        return None
    item = pool[today.toordinal() % len(pool)]
    return Hero(
        url=to_card(item).url,
        name=item.name,
        subtitle=default_subtitle(item),
        overview=item.overview,
        backdrop_path=item.backdrop_path or "",
    )


FeedLoader = Callable[[TMDBClient, date], Awaitable[list[Card]]]

NEW_FEEDS: dict[FeedTab, FeedLoader] = {
    "series": new_series_feed,
    "movies": new_movies_feed,
    "anime": new_anime_feed,
}
POPULAR_FEEDS: dict[FeedTab, FeedLoader] = {
    "series": popular_series_feed,
    "movies": popular_movies_feed,
    "anime": popular_anime_feed,
}
