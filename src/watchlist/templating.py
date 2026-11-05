from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"


def app_context(request: Request) -> dict[str, Any]:
    return {
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "current_user": getattr(request.state, "user", None),
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[app_context])
