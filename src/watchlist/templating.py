from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
STATUS_LABELS = {
    "Returning Series": "Виходить",
    "Ended": "Завершений",
    "Canceled": "Закритий",
    "In Production": "У виробництві",
    "Planned": "Заплановано",
    "Pilot": "Пілот",
    "Released": "Вийшов",
    "Post Production": "Постпродакшн",
    "Rumored": "Чутки",
}


def app_context(request: Request) -> dict[str, Any]:
    return {
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "current_user": getattr(request.state, "user", None),
        "today": datetime.now(UTC).date(),
    }


def poster_url(path: str | None, size: str = "w342") -> str | None:
    if not path:
        return None
    return f"{TMDB_IMAGE_BASE}/{size}{path}"


def format_date(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def status_label(value: str | None) -> str:
    if not value:
        return ""
    return STATUS_LABELS.get(value, value)


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[app_context])
templates.env.filters["poster_url"] = poster_url
templates.env.filters["format_date"] = format_date
templates.env.filters["status_label"] = status_label


def render_error(request: Request, status_code: int, title: str, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"title": title, "message": message}, status_code=status_code
    )
