import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from watchlist import health
from watchlist.auth.dependencies import LoginRequiredError
from watchlist.auth.routes import router as auth_router
from watchlist.catalog.routes import router as catalog_router
from watchlist.config import configure_logging, get_settings
from watchlist.csrf import CSRFError, csrf_middleware
from watchlist.db import get_engine
from watchlist.integrations.dependencies import build_tmdb_client
from watchlist.integrations.tmdb import (
    TMDBError,
    TMDBNotConfiguredError,
    TMDBNotFoundError,
)
from watchlist.templating import render_error
from watchlist.tracking.progress_routes import router as progress_router
from watchlist.tracking.routes import router as tracking_router

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.tmdb_client = build_tmdb_client(get_settings())
    yield
    await app.state.tmdb_client.aclose()
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
    return render_error(
        request,
        status.HTTP_403_FORBIDDEN,
        "Запит відхилено",
        "Сторінка застаріла. Оновіть її та спробуйте ще раз.",
    )


async def handle_tmdb_error(request: Request, error: Exception) -> Response:
    if isinstance(error, TMDBNotFoundError):
        return render_error(
            request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такого тайтлу немає в TMDB."
        )
    if isinstance(error, TMDBNotConfiguredError):
        return render_error(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "TMDB не налаштовано",
            "Не задано ключ TMDB_API_KEY.",
        )
    logger.error("TMDB request failed: %s", error)
    return render_error(
        request,
        status.HTTP_502_BAD_GATEWAY,
        "TMDB недоступний",
        "Не вдалося отримати дані з TMDB. Спробуйте трохи пізніше.",
    )


async def handle_http_error(request: Request, error: Exception) -> Response:
    if not isinstance(error, StarletteHTTPException):
        raise error
    if error.status_code == status.HTTP_404_NOT_FOUND:
        return render_error(
            request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такої сторінки не існує."
        )
    return await http_exception_handler(request, error)


async def handle_validation_error(request: Request, _: Exception) -> Response:
    return render_error(
        request, status.HTTP_400_BAD_REQUEST, "Некоректний запит", "Перевірте адресу сторінки."
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
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
    app.add_exception_handler(TMDBError, handle_tmdb_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(catalog_router)
    app.include_router(tracking_router)
    app.include_router(progress_router)
    return app


app = create_app()
