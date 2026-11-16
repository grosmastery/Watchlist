import logging
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Path, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from watchlist.auth.dependencies import DbSession, RequiredUser
from watchlist.catalog.models import TitleType
from watchlist.catalog.service import find_title_id, get_or_import_title
from watchlist.csrf import csrf_protect
from watchlist.home.routes import Cache, cached_genres
from watchlist.integrations.dependencies import TMDB
from watchlist.integrations.tmdb import TMDBError
from watchlist.swipe.deck import SwipeCard, SwipeCategory, draw_deck
from watchlist.swipe.service import (
    SwipeDecision,
    excluded_keys,
    liked_count,
    list_liked,
    record_swipe,
    remove_liked,
    undo_swipe,
)
from watchlist.templating import templates
from watchlist.tracking.models import ListStatus
from watchlist.tracking.service import add_to_list, remove_from_list

logger = logging.getLogger(__name__)

router = APIRouter(tags=["swipe"])

CSRF = [Depends(csrf_protect)]
Types = Annotated[list[SwipeCategory] | None, Query()]
MediaTypeParam = Literal["tv", "movie"]
TmdbId = Annotated[int, Path(gt=0)]


def ordered(types: list[SwipeCategory] | None) -> list[SwipeCategory]:
    return [category for category in SwipeCategory if types and category in types]


def types_query(selected: list[SwipeCategory]) -> str:
    return urlencode([("types", category.value) for category in selected])


@router.get("/swipe", response_class=HTMLResponse)
async def swipe_page(
    request: Request, user: RequiredUser, session: DbSession, types: Types = None
) -> HTMLResponse:
    context = {
        "categories": list(SwipeCategory),
        "selected": ordered(types) or list(SwipeCategory),
        "liked_count": await liked_count(session, user.id),
    }
    return templates.TemplateResponse(request, "swipe/index.html", context)


@router.get("/swipe/deck", response_class=HTMLResponse)
async def swipe_deck(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    cache: Cache,
    client: TMDB,
    types: Types = None,
) -> HTMLResponse:
    selected = ordered(types)
    cards: list[SwipeCard] = []
    error = False
    if selected:
        try:
            cards = await draw_deck(
                client,
                selected,
                await excluded_keys(session, user.id),
                await cached_genres(cache, client),
            )
        except TMDBError as failure:
            logger.warning("Could not load the swipe deck: %s", failure)
            error = True
    query = types_query(selected)
    context = {"selected": selected, "cards": cards, "error": error, "query": query}
    response = templates.TemplateResponse(request, "swipe/_deck.html", context)
    if selected:
        response.headers["HX-Replace-Url"] = f"/swipe?{query}"
    return response


@router.post("/swipe/decisions", dependencies=CSRF)
async def swipe_decision(
    user: RequiredUser, session: DbSession, decision: Annotated[SwipeDecision, Form()]
) -> JSONResponse:
    await record_swipe(session, user.id, decision)
    await session.commit()
    return JSONResponse({"liked_count": await liked_count(session, user.id)})


@router.delete("/swipe/decisions/{media_type}/{tmdb_id}", dependencies=CSRF)
async def undo_decision(
    user: RequiredUser,
    session: DbSession,
    media_type: Literal["tv", "movie"],
    tmdb_id: Annotated[int, Path(gt=0)],
) -> JSONResponse:
    await undo_swipe(session, user.id, media_type, tmdb_id)
    await session.commit()
    return JSONResponse({"liked_count": await liked_count(session, user.id)})


@router.post("/swipe/list", dependencies=CSRF)
async def swipe_to_list(
    user: RequiredUser,
    session: DbSession,
    client: TMDB,
    media_type: Annotated[MediaTypeParam, Form()],
    tmdb_id: Annotated[int, Form(gt=0)],
) -> JSONResponse:
    try:
        title_id = await get_or_import_title(
            session, client, TitleType.from_tmdb(media_type), tmdb_id
        )
    except TMDBError as failure:
        logger.warning("Could not import a swiped title: %s", failure)
        return JSONResponse({"detail": "tmdb"}, status_code=502)
    await add_to_list(session, user.id, title_id, ListStatus.PLANNED)
    await session.commit()
    return JSONResponse({"liked_count": await liked_count(session, user.id)})


@router.delete("/swipe/list/{media_type}/{tmdb_id}", dependencies=CSRF)
async def undo_swipe_to_list(
    user: RequiredUser, session: DbSession, media_type: MediaTypeParam, tmdb_id: TmdbId
) -> JSONResponse:
    title_id = await find_title_id(session, TitleType.from_tmdb(media_type), tmdb_id)
    if title_id is not None:
        await remove_from_list(session, user.id, title_id)
        await session.commit()
    return JSONResponse({"liked_count": await liked_count(session, user.id)})


@router.get("/swipe/liked", response_class=HTMLResponse)
async def liked_page(request: Request, user: RequiredUser, session: DbSession) -> HTMLResponse:
    context = {"items": await list_liked(session, user.id)}
    return templates.TemplateResponse(request, "swipe/liked.html", context)


@router.delete("/swipe/liked/{media_type}/{tmdb_id}", dependencies=CSRF)
async def remove_from_liked(
    user: RequiredUser,
    session: DbSession,
    media_type: Literal["tv", "movie"],
    tmdb_id: Annotated[int, Path(gt=0)],
) -> Response:
    await remove_liked(session, user.id, media_type, tmdb_id)
    await session.commit()
    if await liked_count(session, user.id):
        return HTMLResponse("")
    return Response(headers={"HX-Refresh": "true"})
