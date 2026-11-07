from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, fetch_csrf_token, register_user
from watchlist.auth.models import User
from watchlist.tracking.models import ListStatus
from watchlist.tracking.service import add_to_list, get_entry

HTMX = {"HX-Request": "true"}


async def login(client: AsyncClient, session: AsyncSession, username: str = "viewer") -> int:
    await register_user(client, email=f"{username}@example.com", username=username)
    user_id = await session.scalar(select(User.id).where(User.username == username))
    assert user_id is not None
    return user_id


async def csrf_headers(client: AsyncClient) -> dict[str, str]:
    return {**HTMX, "X-CSRF-Token": await fetch_csrf_token(client, "/list")}


async def test_list_requires_login(client: AsyncClient) -> None:
    response = await client.get("/list")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


async def test_empty_list(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.get("/list")

    assert response.status_code == 200
    assert "Ваш список порожній" in response.text


async def test_add_title_with_htmx(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)

    response = await client.post(
        f"/list/{title_id}", data={"status": "watching"}, headers=await csrf_headers(client)
    )

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "list-changed"
    assert f'id="entry-{title_id}"' in response.text
    assert "<html" not in response.text
    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.status is ListStatus.WATCHING


async def test_add_title_without_htmx_redirects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login(client, db_session)
    title_id = await create_title(db_session)
    token = await fetch_csrf_token(client, "/list")

    response = await client.post(f"/list/{title_id}", data={"csrf_token": token})

    assert response.status_code == 303
    assert response.headers["location"] == f"/titles/{title_id}"


async def test_add_unknown_title(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.post("/list/999999", headers=await csrf_headers(client))

    assert response.status_code == 404


async def test_add_with_invalid_status(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)
    title_id = await create_title(db_session)

    response = await client.post(
        f"/list/{title_id}", data={"status": "bogus"}, headers=await csrf_headers(client)
    )

    assert response.status_code == 400


async def test_change_status_and_rating(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)
    headers = await csrf_headers(client)

    status_response = await client.patch(
        f"/list/{title_id}", data={"status": "completed"}, headers=headers
    )
    rating_response = await client.patch(f"/list/{title_id}", data={"rating": "8"}, headers=headers)

    assert status_response.status_code == 200
    assert rating_response.status_code == 200
    assert '<option value="8" selected>' in rating_response.text
    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.status is ListStatus.COMPLETED
    assert entry.rating == 8


async def test_clear_rating(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)
    headers = await csrf_headers(client)
    await client.patch(f"/list/{title_id}", data={"rating": "5"}, headers=headers)

    response = await client.patch(f"/list/{title_id}", data={"rating": ""}, headers=headers)

    assert response.status_code == 200
    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.rating is None


async def test_invalid_rating(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)
    headers = await csrf_headers(client)

    for value in ("11", "0", "abc"):
        response = await client.patch(f"/list/{title_id}", data={"rating": value}, headers=headers)
        assert response.status_code == 400


async def test_patch_without_changes(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)

    response = await client.patch(f"/list/{title_id}", headers=await csrf_headers(client))

    assert response.status_code == 400


async def test_remove_from_title_page(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)

    response = await client.delete(f"/list/{title_id}", headers=await csrf_headers(client))

    assert response.status_code == 200
    assert "Додати до списку" in response.text
    assert await get_entry(db_session, user_id, title_id) is None


async def test_remove_from_list_page(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)

    response = await client.delete(
        f"/list/{title_id}?view=list", headers=await csrf_headers(client)
    )

    assert response.status_code == 200
    assert response.text == ""
    assert response.headers["HX-Trigger"] == "list-changed"


async def test_cannot_touch_other_users_entry(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner_id = await login(client, db_session, "owner")
    title_id = await create_title(db_session)
    await add_to_list(db_session, owner_id, title_id, ListStatus.WATCHING)
    await client.post("/logout", headers=await csrf_headers(client))
    await login(client, db_session, "intruder")
    headers = await csrf_headers(client)

    patch = await client.patch(f"/list/{title_id}", data={"status": "dropped"}, headers=headers)
    delete = await client.delete(f"/list/{title_id}", headers=headers)
    page = await client.get("/list")

    assert patch.status_code == 404
    assert delete.status_code == 404
    assert "Тестовий серіал" not in page.text
    entry = await get_entry(db_session, owner_id, title_id)
    assert entry is not None
    assert entry.status is ListStatus.WATCHING


async def test_list_page_filters_by_status(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    watching = await create_title(db_session, "Серіал А", tmdb_id=1)
    planned = await create_title(db_session, "Серіал Б", tmdb_id=2)
    await add_to_list(db_session, user_id, watching, ListStatus.WATCHING)
    await add_to_list(db_session, user_id, planned, ListStatus.PLANNED)

    everything = await client.get("/list")
    filtered = await client.get("/list?status=watching")

    assert "Серіал А" in everything.text
    assert "Серіал Б" in everything.text
    assert "Усі · 2" in everything.text
    assert "Серіал А" in filtered.text
    assert "Серіал Б" not in filtered.text


async def test_invalid_status_filter(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.get("/list?status=bogus")

    assert response.status_code == 400


async def test_list_tabs_fragment(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.DROPPED)

    response = await client.get("/list/tabs?status=dropped", headers=HTMX)

    assert response.status_code == 200
    assert "Закинув · 1" in response.text
    assert "<html" not in response.text


async def test_title_page_shows_list_controls(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)

    before = await client.get(f"/titles/{title_id}")
    await add_to_list(db_session, user_id, title_id, ListStatus.WATCHING)
    after = await client.get(f"/titles/{title_id}")

    assert "Додати до списку" in before.text
    assert "Додати до списку" not in after.text
    assert '<option value="watching" selected>' in after.text


async def test_list_mutations_require_csrf(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)

    patch = await client.patch(f"/list/{title_id}", data={"status": "dropped"}, headers=HTMX)
    delete = await client.delete(f"/list/{title_id}", headers=HTMX)

    assert patch.status_code == 403
    assert delete.status_code == 403
