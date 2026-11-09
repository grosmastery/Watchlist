import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from watchlist.auth.dependencies import CurrentUser, DbSession, RequiredUser
from watchlist.home.cache import TTLCache
from watchlist.home.feeds import (
    NEW_FEEDS,
    POPULAR_FEEDS,
    Card,
    FeedLoader,
    FeedTab,
    Hero,
    hero_feed,
)
from watchlist.home.recommendations import (
    GenreNames,
    fingerprint,
    load_signals,
    recommend,
    select_seeds,
)
from watchlist.integrations.dependencies import TMDB
from watchlist.integrations.tmdb import TMDBClient, TMDBError
from watchlist.templating import templates
from watchlist.tracking.progress import utc_today

logger = logging.getLogger(__name__)

router = APIRouter(tags=["home"])

FEED_TTL = 6 * 3600
RECOMMENDATIONS_TTL = 6 * 3600
GENRES_TTL = 24 * 3600
TABS: list[tuple[FeedTab, str]] = [("series", "Серіали"), ("movies", "Фільми"), ("anime", "Аніме")]
Section = Literal["new", "popular"]
Tab = Annotated[FeedTab, Query()]


def get_home_cache(request: Request) -> TTLCache:
    cache: TTLCache = request.app.state.home_cache
    return cache


Cache = Annotated[TTLCache, Depends(get_home_cache)]


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request, user: CurrentUser) -> HTMLResponse:
    return templates.TemplateResponse(request, "home/index.html")


async def feed_section(
    request: Request,
    cache: TTLCache,
    client: TMDBClient,
    section: Section,
    heading: str,
    tab: FeedTab,
    loader: FeedLoader,
) -> HTMLResponse:
    today = utc_today()
    cards: list[Card] = []
    error = False
    try:
        cards = await cache.get_or_load((section, tab), FEED_TTL, lambda: loader(client, today))
    except TMDBError as failure:
        logger.warning("Could not load the %s feed: %s", section, failure)
        error = True
    context = {
        "section": section,
        "heading": heading,
        "tabs": TABS,
        "current_tab": tab,
        "cards": cards,
        "error": error,
    }
    return templates.TemplateResponse(request, "home/_feed_section.html", context)


@router.get("/home/hero", response_class=HTMLResponse)
async def hero_section(request: Request, cache: Cache, client: TMDB) -> HTMLResponse:
    today = utc_today()
    hero: Hero | None = None
    try:
        hero = await cache.get_or_load("hero", FEED_TTL, lambda: hero_feed(client, today))
    except TMDBError as failure:
        logger.warning("Could not load the hero banner: %s", failure)
    if hero is None:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "home/_hero.html", {"hero": hero})


@router.get("/home/new", response_class=HTMLResponse)
async def new_section(
    request: Request, cache: Cache, client: TMDB, tab: Tab = "series"
) -> HTMLResponse:
    return await feed_section(request, cache, client, "new", "Новинки", tab, NEW_FEEDS[tab])


@router.get("/home/popular", response_class=HTMLResponse)
async def popular_section(
    request: Request, cache: Cache, client: TMDB, tab: Tab = "series"
) -> HTMLResponse:
    return await feed_section(
        request, cache, client, "popular", "Популярні", tab, POPULAR_FEEDS[tab]
    )


async def cached_genres(cache: TTLCache, client: TMDBClient) -> GenreNames:
    async def load() -> GenreNames:
        return {"tv": await client.get_genres("tv"), "movie": await client.get_genres("movie")}

    return await cache.get_or_load("genres", GENRES_TTL, load)


@router.get("/home/recommendations", response_class=HTMLResponse)
async def recommendations_section(
    request: Request, user: RequiredUser, session: DbSession, cache: Cache, client: TMDB
) -> HTMLResponse:
    signals = await load_signals(session, user.id)
    has_seeds = bool(select_seeds(signals))
    cards: list[Card] = []
    error = False

    async def load() -> list[Card]:
        return await recommend(client, signals, await cached_genres(cache, client))

    if has_seeds:
        try:
            cards = await cache.get_or_load(
                ("recommendations", user.id, fingerprint(signals)), RECOMMENDATIONS_TTL, load
            )
        except TMDBError as failure:
            logger.warning("Could not load recommendations: %s", failure)
            error = True
    context = {"cards": cards, "has_seeds": has_seeds, "error": error}
    return templates.TemplateResponse(request, "home/_recommendations.html", context)
