import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from watchlist import health
from watchlist.auth.dependencies import LoginRequiredError
from watchlist.auth.routes import router as auth_router
from watchlist.config import get_settings
from watchlist.csrf import CSRFError, csrf_middleware
from watchlist.db import get_engine
from watchlist.templating import templates
from watchlist.tracking.routes import router as tracking_router

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await get_engine().dispose()


async def handle_login_required(request: Request, _: Exception) -> Response:
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    login_url = "/login" if target == "/" else f"/login?{urlencode({'next': target})}"
    if request.headers.get("HX-Request"):
        return Response(headers={"HX-Redirect": login_url})
    return RedirectResponse(login_url, status_code=status.HTTP_303_SEE_OTHER)


async def handle_csrf_error(request: Request, _: Exception) -> Response:
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "title": "Запит відхилено",
            "message": "Сторінка застаріла. Оновіть її та спробуйте ще раз.",
        },
        status_code=status.HTTP_403_FORBIDDEN,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    app = FastAPI(
        title="Watchlist",
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    app.middleware("http")(csrf_middleware)
    app.add_exception_handler(LoginRequiredError, handle_login_required)
    app.add_exception_handler(CSRFError, handle_csrf_error)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(tracking_router)
    return app


app = create_app()
