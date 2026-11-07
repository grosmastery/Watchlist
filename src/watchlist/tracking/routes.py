from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from watchlist.auth.dependencies import DbSession, RequiredUser
from watchlist.csrf import csrf_protect
from watchlist.templating import render_error, templates
from watchlist.tracking.models import ListStatus, UserTitle
from watchlist.tracking.service import (
    UNSET,
    EntryNotFoundError,
    InvalidRatingError,
    TitleNotFoundError,
    Unset,
    add_to_list,
    count_by_status,
    list_entries,
    remove_from_list,
    update_entry,
)

router = APIRouter(tags=["tracking"])

LIST_CHANGED_EVENT = "list-changed"

View = Annotated[Literal["title", "list"], Query()]
StatusFilter = Annotated[ListStatus | None, Query(alias="status")]


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def controls_response(
    request: Request, title_id: int, entry: UserTitle | None, view: str
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        "tracking/_entry_controls.html",
        {"title_id": title_id, "entry": entry, "view": view},
    )
    response.headers["HX-Trigger"] = LIST_CHANGED_EVENT
    return response


def bad_request(request: Request, message: str) -> HTMLResponse:
    return render_error(request, status.HTTP_400_BAD_REQUEST, "Некоректний запит", message)


def not_found(request: Request) -> HTMLResponse:
    return render_error(
        request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такого тайтлу немає у вашому списку."
    )


def parse_status(value: str) -> ListStatus | None:
    try:
        return ListStatus(value)
    except ValueError:
        return None


def form_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def parse_rating(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if not value.isdigit():
        raise InvalidRatingError(value)
    return int(value)


@router.get("/list", response_class=HTMLResponse)
async def my_list(
    request: Request, user: RequiredUser, session: DbSession, status_filter: StatusFilter = None
) -> HTMLResponse:
    context = {
        "entries": await list_entries(session, user.id, status_filter),
        "counts": await count_by_status(session, user.id),
        "current_status": status_filter,
    }
    return templates.TemplateResponse(request, "tracking/list.html", context)


@router.get("/list/tabs", response_class=HTMLResponse)
async def list_tabs(
    request: Request, user: RequiredUser, session: DbSession, status_filter: StatusFilter = None
) -> HTMLResponse:
    context = {"counts": await count_by_status(session, user.id), "current_status": status_filter}
    return templates.TemplateResponse(request, "tracking/_list_tabs.html", context)


@router.post("/list/{title_id}", dependencies=[Depends(csrf_protect)], response_model=None)
async def add_title(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    title_id: int,
    view: View = "title",
    status_value: Annotated[str, Form(alias="status")] = ListStatus.PLANNED.value,
) -> Response:
    list_status = parse_status(status_value)
    if list_status is None:
        return bad_request(request, "Невідомий статус.")
    try:
        entry = await add_to_list(session, user.id, title_id, list_status)
    except TitleNotFoundError:
        return render_error(
            request, status.HTTP_404_NOT_FOUND, "Не знайдено", "Такого тайтлу немає в каталозі."
        )
    await session.commit()
    if not is_htmx(request):
        return RedirectResponse(f"/titles/{title_id}", status_code=status.HTTP_303_SEE_OTHER)
    return controls_response(request, title_id, entry, view)


@router.patch("/list/{title_id}", dependencies=[Depends(csrf_protect)], response_model=None)
async def change_entry(
    request: Request,
    user: RequiredUser,
    session: DbSession,
    title_id: int,
    view: View = "title",
) -> Response:
    form = await request.form()
    status_value = form_text(form.get("status"))
    rating_value = form_text(form.get("rating"))
    new_status: ListStatus | Unset = UNSET
    new_rating: int | Unset | None = UNSET
    if status_value is not None:
        parsed = parse_status(status_value)
        if parsed is None:
            return bad_request(request, "Невідомий статус.")
        new_status = parsed
    if rating_value is not None:
        try:
            new_rating = parse_rating(rating_value)
        except InvalidRatingError:
            return bad_request(request, "Оцінка має бути числом від 1 до 10.")
    if isinstance(new_status, Unset) and isinstance(new_rating, Unset):
        return bad_request(request, "Немає змін.")
    try:
        entry = await update_entry(session, user.id, title_id, status=new_status, rating=new_rating)
    except EntryNotFoundError:
        return not_found(request)
    except InvalidRatingError:
        return bad_request(request, "Оцінка має бути числом від 1 до 10.")
    await session.commit()
    return controls_response(request, title_id, entry, view)


@router.delete("/list/{title_id}", dependencies=[Depends(csrf_protect)], response_model=None)
async def remove_title(
    request: Request, user: RequiredUser, session: DbSession, title_id: int, view: View = "title"
) -> Response:
    if not await remove_from_list(session, user.id, title_id):
        return not_found(request)
    await session.commit()
    if view == "list":
        return Response(headers={"HX-Trigger": LIST_CHANGED_EVENT})
    return controls_response(request, title_id, None, view)
