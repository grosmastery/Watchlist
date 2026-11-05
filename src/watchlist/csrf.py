from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from watchlist.auth.security import constant_time_equals, generate_token, sign
from watchlist.config import get_settings

CSRF_COOKIE = "watchlist_csrf"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"
FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


class CSRFError(Exception):
    pass


def form_token_for(cookie_value: str) -> str:
    return sign(cookie_value, get_settings().secret_key.get_secret_value())


async def csrf_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    cookie_value = request.cookies.get(CSRF_COOKIE)
    is_new = not cookie_value
    if not cookie_value:
        cookie_value = generate_token()
    request.state.csrf_token = form_token_for(cookie_value)
    response = await call_next(request)
    if is_new:
        response.set_cookie(
            CSRF_COOKIE,
            cookie_value,
            httponly=True,
            samesite="lax",
            secure=get_settings().secure_cookies,
        )
    return response


async def csrf_protect(request: Request) -> None:
    cookie_value = request.cookies.get(CSRF_COOKIE)
    if not cookie_value:
        raise CSRFError
    provided = request.headers.get(CSRF_HEADER)
    if provided is None and request.headers.get("content-type", "").startswith(FORM_CONTENT_TYPES):
        value = (await request.form()).get(CSRF_FIELD)
        provided = value if isinstance(value, str) else None
    if not provided or not constant_time_equals(provided, form_token_for(cookie_value)):
        raise CSRFError
