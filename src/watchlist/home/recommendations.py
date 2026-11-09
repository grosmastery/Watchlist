import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.catalog.models import Title
from watchlist.home.feeds import Card, interleave, is_listable, to_card
from watchlist.integrations.tmdb import TMDBClient, TMDBError, TMDBUnavailableError
from watchlist.integrations.tmdb_schemas import BrowseItem, MediaType
from watchlist.tracking.models import ListStatus, UserTitle

SEED_LIMIT = 8
RESULT_LIMIT = 20
MIN_RESULTS = 8
PER_SOURCE_LIMIT = 3
RECOMMENDATIONS_PER_SEED = 20
MIN_VOTES = 10
FALLBACK_MIN_VOTES = 100
FALLBACK_GENRES = 2
GENRE_INFLUENCE = 0.5
LIKED_WEIGHT = 2.0

TitleKey = tuple[MediaType, int]
GenreNames = dict[MediaType, dict[int, str]]


@dataclass(frozen=True, slots=True)
class Signal:
    key: TitleKey
    name: str
    genres: tuple[str, ...]
    weight: float
    updated_at: datetime


@dataclass(slots=True)
class Candidate:
    item: BrowseItem
    score: float = 0.0
    source: Signal | None = None
    source_share: float = field(default=0.0)


def signal_weight(status: ListStatus, rating: int | None) -> float:
    if status is ListStatus.DROPPED or (rating is not None and rating <= 4):
        return -2.0
    if rating is not None:
        if rating >= 9:
            return 3.0
        return 2.0 if rating >= 7 else 0.0
    return 0.5 if status is ListStatus.PLANNED else 1.0


async def load_signals(session: AsyncSession, user_id: int) -> list[Signal]:
    rows = await session.execute(
        select(UserTitle.status, UserTitle.rating, UserTitle.updated_at, Title)
        .join(Title, Title.id == UserTitle.title_id)
        .where(UserTitle.user_id == user_id)
    )
    return [
        Signal(
            key=(title.type.tmdb_type, title.tmdb_id),
            name=title.name,
            genres=tuple(title.genres),
            weight=signal_weight(status, rating),
            updated_at=updated_at,
        )
        for status, rating, updated_at, title in rows
    ]


def fingerprint(signals: list[Signal]) -> tuple[tuple[TitleKey, float], ...]:
    return tuple(sorted((signal.key, signal.weight) for signal in signals))


def select_seeds(signals: list[Signal]) -> list[Signal]:
    positive = [signal for signal in signals if signal.weight > 0]
    positive.sort(key=lambda signal: (signal.weight, signal.updated_at), reverse=True)
    return positive[:SEED_LIMIT]


def genre_profile(signals: list[Signal]) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    for signal in signals:
        for genre in signal.genres:
            totals[genre] += signal.weight
    scale = max((abs(value) for value in totals.values()), default=0.0)
    return {genre: value / scale for genre, value in totals.items()} if scale else {}


def genre_factor(names: list[str], profile: dict[str, float]) -> float:
    if not names:
        return 1.0
    return 1 + GENRE_INFLUENCE * sum(profile.get(name, 0.0) for name in names) / len(names)


def item_genres(item: BrowseItem, genres: GenreNames) -> list[str]:
    names = genres.get(item.media_type, {})
    return [names[genre] for genre in item.genre_ids if genre in names]


def reason(source: Signal) -> str:
    if source.weight >= LIKED_WEIGHT:
        return f"Бо вам сподобалось «{source.name}»"
    return f"Схоже на «{source.name}»"


def collect_candidates(
    seed_results: list[tuple[Signal, list[BrowseItem]]], known: set[TitleKey]
) -> dict[TitleKey, Candidate]:
    candidates: dict[TitleKey, Candidate] = {}
    for seed, items in seed_results:
        for position, item in enumerate(items):
            key = (item.media_type, item.id)
            if key in known or not is_listable(item) or item.vote_count < MIN_VOTES:
                continue
            share = seed.weight * (1 - position / RECOMMENDATIONS_PER_SEED)
            candidate = candidates.setdefault(key, Candidate(item))
            candidate.score += share
            if share > candidate.source_share:
                candidate.source = seed
                candidate.source_share = share
    return candidates


def rank(
    seed_results: list[tuple[Signal, list[BrowseItem]]],
    signals: list[Signal],
    genres: GenreNames,
) -> list[Card]:
    known = {signal.key for signal in signals}
    profile = genre_profile(signals)
    candidates = collect_candidates(seed_results, known)
    ordered = sorted(
        candidates.values(),
        key=lambda candidate: (
            candidate.score * genre_factor(item_genres(candidate.item, genres), profile)
        ),
        reverse=True,
    )
    used: Counter[TitleKey] = Counter()
    cards: list[Card] = []
    for candidate in ordered:
        source = candidate.source
        if source is None or used[source.key] >= PER_SOURCE_LIMIT:
            continue
        used[source.key] += 1
        cards.append(to_card(candidate.item, note=reason(source)))
        if len(cards) == RESULT_LIMIT:
            break
    return cards


async def seed_recommendations(
    client: TMDBClient, seed: Signal
) -> tuple[Signal, list[BrowseItem]] | None:
    media_type, tmdb_id = seed.key
    try:
        return seed, await client.get_recommendations(media_type, tmdb_id)
    except TMDBError:
        return None


async def genre_fallback(
    client: TMDBClient, signals: list[Signal], genres: GenreNames, taken: set[TitleKey]
) -> list[Card]:
    profile = genre_profile(signals)
    favourites = sorted(
        (name for name, value in profile.items() if value > 0),
        key=lambda name: profile[name],
        reverse=True,
    )[:FALLBACK_GENRES]
    known = taken | {signal.key for signal in signals}

    async def fetch(media_type: MediaType) -> list[Card]:
        ids = {genre: name for genre, name in genres[media_type].items() if name in favourites}
        if not ids:
            return []
        items = await client.discover(
            media_type,
            {
                "with_genres": "|".join(str(genre) for genre in ids),
                "vote_count.gte": FALLBACK_MIN_VOTES,
            },
        )
        return [
            to_card(
                item,
                note=f"Популярне в жанрі «{next(ids[g] for g in item.genre_ids if g in ids)}»",
            )
            for item in items
            if (item.media_type, item.id) not in known
            and is_listable(item)
            and any(genre in ids for genre in item.genre_ids)
        ]

    series, movies = await asyncio.gather(fetch("tv"), fetch("movie"))
    return interleave(series, movies)


async def recommend(client: TMDBClient, signals: list[Signal], genres: GenreNames) -> list[Card]:
    seeds = select_seeds(signals)
    responses = await asyncio.gather(*(seed_recommendations(client, seed) for seed in seeds))
    seed_results = [response for response in responses if response is not None]
    if seeds and not seed_results:
        raise TMDBUnavailableError("No recommendations could be loaded")
    cards = rank(seed_results, signals, genres)
    if len(cards) >= MIN_RESULTS:
        return cards
    extra = await genre_fallback(client, signals, genres, {card.key for card in cards})
    return [*cards, *extra][:RESULT_LIMIT]
