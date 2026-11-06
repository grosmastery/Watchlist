import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from watchlist.auth.dependencies import DbSession, RequiredUser
from watchlist.catalog.models import TitleType
from watchlist.catalog.service import get_or_import_title, get_title, search_titles
from watchlist.integrations.dependencies import TMDB
from watchlist.integrations.tmdb import TMDBError, TMDBNotConfiguredError
from watchlist.integrations.tmdb_schemas import SearchResult
from watchlist.templating import render_error, templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["catalog"])

QUERY_MAX_LENGTH = 200


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    user: RequiredUser,
    client: TMDB,
    q: Annotated[str, Query(max_length=QUERY_MAX_LENGTH)] = "",
) -> HTMLResponse:
    query = q.strip()
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
    template = (
        "catalog/_search_results.html"
        if request.headers.get("HX-Request")
        else "catalog/search.html"
    )
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
    return templates.TemplateResponse(request, "catalog/title.html", {"title": title})
