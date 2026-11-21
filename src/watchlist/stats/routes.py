from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from watchlist.auth.dependencies import DbSession, RequiredUser
from watchlist.stats.service import user_stats
from watchlist.templating import templates
from watchlist.tracking.progress import utc_today

router = APIRouter(tags=["stats"])


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, user: RequiredUser, session: DbSession) -> HTMLResponse:
    context = {"stats": await user_stats(session, user.id, utc_today())}
    return templates.TemplateResponse(request, "stats/index.html", context)
