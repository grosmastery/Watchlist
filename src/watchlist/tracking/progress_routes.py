from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import HTMLResponse, Response

from watchlist.auth.dependencies import DbSession, RequiredUser
from watchlist.catalog.models import Season
from watchlist.csrf import csrf_protect
from watchlist.templating import render_error, templates
from watchlist.tracking.progress import (
    EpisodeNotAiredError,
    EpisodeNotFoundError,
    SeasonNotFoundError,
    get_season,
    mark_episode,
    mark_season,
    unmark_episode,
    unmark_season,
    up_next,
    up_next_for_title,
    utc_today,
    watched_episode_ids,
)
from watchlist.tracking.service import get_entry

router = APIRouter(tags=["progress"])

ProgressView = Annotated[Literal["title", "next"], Query()]
EpisodeId = Annotated[int, Path(gt=0)]
SeasonId = Annotated[int, Path(gt=0)]
CSRF = [Depends(csrf_protect)]


@router.get("/up-next", response_class=HTMLResponse)
async def up_next_page(request: Request, user: RequiredUser, session: DbSession) -> HTMLResponse:
    today = utc_today()
    items = await up_next(session, user.id, today)
    context = {
        "available": [item for item in items if item.is_available(today)],
        "upcoming": sorted(
            (item for item in items if item.is_upcoming(today)),
            key=lambda item: item.next_air_date,
        ),
        "waiting": [
            item for item in items if not item.is_available(today) and not item.is_upcoming(today)
        ],
    }
    return templates.TemplateResponse(request, "tracking/up_next.html", context)


async def progress_response(
    request: Request, session: DbSession, user_id: int, season: Season, view: str
) -> Response:
    today = utc_today()
    title_id = season.title_id
    if view == "next":
        item = await up_next_for_title(session, user_id, title_id, today)
        return templates.TemplateResponse(request, "tracking/_next_card.html", {"item": item})
    season = await get_season(session, season.id)
    watched = await watched_episode_ids(
        session, user_id, [episode.id for episode in season.episodes]
    )
    item = await up_next_for_title(session, user_id, title_id, today)
    context = {
        "season": season,
        "watched_ids": watched,
        "title": season.title,
        "entry": await get_entry(session, user_id, title_id),
        "progress": item.progress if item else None,
        "next_episode": item.next_episode if item else None,
    }
    return templates.TemplateResponse(request, "tracking/_season_update.html", context)


def season_not_found(request: Request) -> HTMLResponse:
    return render_error(request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такого сезону немає.")


def episode_error(request: Request, error: Exception) -> HTMLResponse:
    if isinstance(error, EpisodeNotAiredError):
        return render_error(
            request,
            status.HTTP_400_BAD_REQUEST,
            "Серія ще не вийшла",
            "Позначати можна лише серії, які вже вийшли.",
        )
    return render_error(request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такої серії немає.")


@router.post("/episodes/{episode_id}/watched", dependencies=CSRF, response_model=None)
async def watch_episode(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    episode_id: EpisodeId,
    view: ProgressView = "title",
) -> Response:
    try:
        episode = await mark_episode(session, user.id, episode_id)
    except (EpisodeNotFoundError, EpisodeNotAiredError) as error:
        return episode_error(request, error)
    await session.commit()
    return await progress_response(request, session, user.id, episode.season, view)


@router.delete("/episodes/{episode_id}/watched", dependencies=CSRF, response_model=None)
async def unwatch_episode(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    episode_id: EpisodeId,
    view: ProgressView = "title",
) -> Response:
    try:
        episode = await unmark_episode(session, user.id, episode_id)
    except EpisodeNotFoundError as error:
        return episode_error(request, error)
    await session.commit()
    return await progress_response(request, session, user.id, episode.season, view)


@router.post("/seasons/{season_id}/watched", dependencies=CSRF, response_model=None)
async def watch_season(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    season_id: SeasonId,
    view: ProgressView = "title",
) -> Response:
    try:
        season = await mark_season(session, user.id, season_id)
    except SeasonNotFoundError:
        return render_error(
            request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такого сезону немає."
        )
    except EpisodeNotAiredError as error:
        return episode_error(request, error)
    await session.commit()
    return await progress_response(request, session, user.id, season, view)


@router.delete("/seasons/{season_id}/watched", dependencies=CSRF, response_model=None)
async def unwatch_season(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    season_id: SeasonId,
    view: ProgressView = "title",
) -> Response:
    try:
        season = await unmark_season(session, user.id, season_id)
    except SeasonNotFoundError:
        return render_error(
            request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такого сезону немає."
        )
    await session.commit()
    return await progress_response(request, session, user.id, season, view)
