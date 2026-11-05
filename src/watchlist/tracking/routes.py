from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from watchlist.auth.dependencies import RequiredUser
from watchlist.templating import templates

router = APIRouter(tags=["tracking"])


@router.get("/", response_class=HTMLResponse)
async def up_next(request: Request, user: RequiredUser) -> HTMLResponse:
    return templates.TemplateResponse(request, "tracking/up_next.html", {"user": user})
