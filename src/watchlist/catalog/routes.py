import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from watchlist.auth.dependencies import CurrentUser, DbSession, RequiredUser
from watchlist.catalog.cast import find_cast
from watchlist.catalog.models import Title, TitleType
from watchlist.catalog.service import (
    get_or_import_title,
    get_title,
    import_title,
    search_titles,
)
from watchlist.catalog.similar import find_similar
from watchlist.catalog.trailers import find_trailer
from watchlist.csrf import csrf_protect
from watchlist.home.routes import Cache
from watchlist.integrations.dependencies import TMDB
from watchlist.integrations.tmdb import TMDBError, TMDBNotConfiguredError
from watchlist.integrations.tmdb_schemas import SearchResult
from watchlist.templating import render_error, templates
from watchlist.tracking.progress import up_next_for_title, watched_ids_for_title
from watchlist.tracking.service import get_entry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["catalog"])

QUERY_MAX_LENGTH = 200
TRAILER_TTL = 24 * 3600
SIMILAR_TTL = 24 * 3600
CAST_TTL = 24 * 3600
CSRF = [Depends(csrf_protect)]


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    user: CurrentUser,
    client: TMDB,
    q: Annotated[str, Query(max_length=QUERY_MAX_LENGTH)] = "",
) -> HTMLResponse:
    query = q.strip()
    is_htmx = bool(request.headers.get("HX-Request"))
    if is_htmx and not query:
        return HTMLResponse("")
    results: list[SearchResult] = []
    error: str | None = None
    if query:
        try:
            results = await search_titles(client, query)
        except TMDBNotConfiguredError:
            error = "Пошук не налаштований: не задано ключ TMDB."
        except TMDBError:
            logger.exception("TMDB search failed")
            error = "Пошук тимчасово недоступний. Спробуйте трохи пізніше."
    context = {"query": query, "results": results, "error": error}
    template = "catalog/_search_suggestions.html" if is_htmx else "catalog/search.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/tmdb/{media_type}/{tmdb_id}")
async def open_tmdb_title(
    user: RequiredUser,
    session: DbSession,
    client: TMDB,
    media_type: Literal["tv", "movie"],
    tmdb_id: Annotated[int, Path(gt=0)],
) -> RedirectResponse:
    title_id = await get_or_import_title(session, client, TitleType.from_tmdb(media_type), tmdb_id)
    await session.commit()
    return RedirectResponse(f"/titles/{title_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/titles/{title_id}/refresh", dependencies=[Depends(csrf_protect)], response_model=None
)
async def refresh_title(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    client: TMDB,
    title_id: Annotated[int, Path(gt=0)],
) -> Response:
    title = await session.get(Title, title_id)
    if title is None:
        return render_error(
            request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такого тайтлу немає в каталозі."
        )
    await import_title(session, client, title.type, title.tmdb_id)
    await session.commit()
    return RedirectResponse(f"/titles/{title_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/titles/{title_id}/trailer", response_class=HTMLResponse)
async def title_trailer(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    cache: Cache,
    client: TMDB,
    title_id: Annotated[int, Path(gt=0)],
) -> HTMLResponse:
    title = await session.get(Title, title_id)
    if title is None:
        return HTMLResponse("")
    media_type = title.type.tmdb_type
    try:
        key = await cache.get_or_load(
            ("trailer", media_type, title.tmdb_id),
            TRAILER_TTL,
            lambda: find_trailer(client, media_type, title.tmdb_id),
        )
    except TMDBError as failure:
        logger.warning("Could not load the trailer for title %s: %s", title_id, failure)
        return HTMLResponse("")
    if key is None:
        return HTMLResponse("")
    context = {"title": title, "trailer_key": key}
    return templates.TemplateResponse(request, "catalog/_trailer.html", context)


@router.get("/titles/{title_id}/similar", response_class=HTMLResponse)
async def similar_titles(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    cache: Cache,
    client: TMDB,
    title_id: Annotated[int, Path(gt=0)],
) -> HTMLResponse:
    title = await session.get(Title, title_id)
    if title is None:
        return HTMLResponse("")
    media_type = title.type.tmdb_type
    try:
        cards = await cache.get_or_load(
            ("similar", media_type, title.tmdb_id),
            SIMILAR_TTL,
            lambda: find_similar(client, media_type, title.tmdb_id),
        )
    except TMDBError as failure:
        logger.warning("Could not load similar titles for title %s: %s", title_id, failure)
        return HTMLResponse("")
    if not cards:
        return HTMLResponse("")
    context = {"cards": cards, "error": False}
    return templates.TemplateResponse(request, "catalog/_similar.html", context)


@router.get("/titles/{title_id}/cast", response_class=HTMLResponse)
async def title_cast(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    cache: Cache,
    client: TMDB,
    title_id: Annotated[int, Path(gt=0)],
) -> HTMLResponse:
    title = await session.get(Title, title_id)
    if title is None:
        return HTMLResponse("")
    media_type = title.type.tmdb_type
    try:
        actors = await cache.get_or_load(
            ("cast", media_type, title.tmdb_id),
            CAST_TTL,
            lambda: find_cast(client, media_type, title.tmdb_id),
        )
    except TMDBError as failure:
        logger.warning("Could not load the cast for title %s: %s", title_id, failure)
        return HTMLResponse("")
    if not actors:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "catalog/_cast.html", {"actors": actors})


@router.get("/titles/{title_id}", response_class=HTMLResponse)
async def title_page(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    title_id: Annotated[int, Path(gt=0)],
) -> HTMLResponse:
    title = await get_title(session, title_id)
    if title is None:
        return render_error(
            request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такого тайтлу немає в каталозі."
        )
    context: dict[str, object] = {
        "title": title,
        "entry": await get_entry(session, user.id, title.id),
        "watched_ids": set(),
        "progress": None,
        "next_episode": None,
    }
    if title.is_series:
        context["watched_ids"] = await watched_ids_for_title(session, user.id, title.id)
        item = await up_next_for_title(session, user.id, title.id)
        if item is not None:
            context["progress"] = item.progress
            context["next_episode"] = item.next_episode
    return templates.TemplateResponse(request, "catalog/title.html", context)
