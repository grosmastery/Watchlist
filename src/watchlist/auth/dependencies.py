from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.auth.models import User
from watchlist.auth.service import get_user_by_session_token
from watchlist.config import get_settings
from watchlist.db import get_session

SESSION_COOKIE = "watchlist_session"


class LoginRequiredError(Exception):
    pass


def session_ttl() -> timedelta:
    return timedelta(days=get_settings().session_ttl_days)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(session_ttl().total_seconds()),
        httponly=True,
        samesite="lax",
        secure=get_settings().secure_cookies,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, httponly=True, samesite="lax", secure=get_settings().secure_cookies
    )


async def get_current_user(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    user = await get_user_by_session_token(session, token) if token else None
    request.state.user = user
    return user


async def require_user(user: Annotated[User | None, Depends(get_current_user)]) -> User:
    if user is None:
        raise LoginRequiredError
    return user


CurrentUser = Annotated[User | None, Depends(get_current_user)]
RequiredUser = Annotated[User, Depends(require_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
