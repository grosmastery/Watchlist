from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from watchlist.auth.dependencies import (
    SESSION_COOKIE,
    CurrentUser,
    DbSession,
    clear_session_cookie,
    session_ttl,
    set_session_cookie,
)
from watchlist.auth.schemas import LoginForm, RegisterForm, form_errors
from watchlist.auth.service import (
    RegistrationError,
    authenticate,
    create_session,
    delete_session,
    register_user,
)
from watchlist.csrf import csrf_protect
from watchlist.templating import templates

router = APIRouter(tags=["auth"])

FormField = Annotated[str, Form()]


def safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def render_register(
    request: Request,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/register.html",
        {"values": values or {}, "errors": errors or {}},
        status_code=status_code,
    )


def render_login(
    request: Request,
    next_url: str,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"values": values or {}, "errors": errors or {}, "next": next_url},
        status_code=status_code,
    )


@router.get("/register", response_model=None)
async def register_page(request: Request, user: CurrentUser) -> Response:
    if user is not None:
        return redirect("/")
    return render_register(request)


@router.post("/register", dependencies=[Depends(csrf_protect)], response_model=None)
async def register(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    email: FormField = "",
    username: FormField = "",
    password: FormField = "",
    password_confirm: FormField = "",
) -> Response:
    if user is not None:
        return redirect("/")
    values = {"email": email, "username": username}
    try:
        form = RegisterForm(
            email=email, username=username, password=password, password_confirm=password_confirm
        )
        new_user = await register_user(session, form)
    except ValidationError as error:
        return render_register(request, values, form_errors(error), status.HTTP_400_BAD_REQUEST)
    except RegistrationError as error:
        await session.rollback()
        return render_register(request, values, error.errors, status.HTTP_400_BAD_REQUEST)
    token = await create_session(session, new_user, session_ttl())
    await session.commit()
    response = redirect("/")
    set_session_cookie(response, token)
    return response


@router.get("/login", response_model=None)
async def login_page(request: Request, user: CurrentUser, next: str = "/") -> Response:
    if user is not None:
        return redirect(safe_next(next))
    return render_login(request, safe_next(next))


@router.post("/login", dependencies=[Depends(csrf_protect)], response_model=None)
async def login(
    request: Request,
    session: DbSession,
    login: FormField = "",
    password: FormField = "",
    next: FormField = "/",
) -> Response:
    next_url = safe_next(next)
    values = {"login": login}
    try:
        form = LoginForm(login=login, password=password)
    except ValidationError as error:
        return render_login(
            request, next_url, values, form_errors(error), status.HTTP_400_BAD_REQUEST
        )
    user = await authenticate(session, form)
    if user is None:
        return render_login(
            request,
            next_url,
            values,
            {"__all__": "Неправильний логін або пароль"},
            status.HTTP_400_BAD_REQUEST,
        )
    token = await create_session(session, user, session_ttl())
    await session.commit()
    response = redirect(next_url)
    set_session_cookie(response, token)
    return response


@router.post("/logout", dependencies=[Depends(csrf_protect)])
async def logout(request: Request, session: DbSession) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await delete_session(session, token)
        await session.commit()
    response = redirect("/login")
    clear_session_cookie(response)
    return response
