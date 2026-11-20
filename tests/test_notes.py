from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title
from tests.test_tracking_routes import csrf_headers, login
from watchlist.tracking.models import NOTE_MAX_LENGTH, ListStatus
from watchlist.tracking.service import add_to_list, get_entry


async def test_note_is_saved_and_shown(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.WATCHING)

    response = await client.post(
        f"/list/{title_id}/note",
        data={"note": "  Перший сезон слабкий,\r\nдалі краще  "},
        headers=await csrf_headers(client),
    )

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "list-changed"
    assert f'id="note-{title_id}"' in response.text
    assert f'id="entry-{title_id}" hx-swap-oob="true"' in response.text
    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.note == "Перший сезон слабкий,\nдалі краще"
    assert entry.status is ListStatus.WATCHING
    title_page = await client.get(f"/titles/{title_id}")
    assert "Перший сезон слабкий" in title_page.text
    assert "Змінити" in title_page.text
    list_page = await client.get("/list")
    assert "Перший сезон слабкий" in list_page.text


async def test_note_on_unlisted_title_adds_it_as_planned(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)

    response = await client.post(
        f"/list/{title_id}/note", data={"note": "Порадив друг"}, headers=await csrf_headers(client)
    )

    assert f'id="rating-{title_id}" hx-swap-oob="true"' in response.text
    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.status is ListStatus.PLANNED
    assert entry.note == "Порадив друг"


async def test_empty_note_on_unlisted_title_does_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)

    response = await client.post(
        f"/list/{title_id}/note", data={"note": "   "}, headers=await csrf_headers(client)
    )

    assert response.status_code == 200
    assert "Додати нотатку" in response.text
    assert await get_entry(db_session, user_id, title_id) is None


async def test_blank_note_clears_it(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.COMPLETED)
    headers = await csrf_headers(client)
    await client.post(f"/list/{title_id}/note", data={"note": "Шедевр"}, headers=headers)

    await client.post(f"/list/{title_id}/note", data={"note": ""}, headers=headers)

    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.note is None


async def test_too_long_note_is_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.COMPLETED)

    response = await client.post(
        f"/list/{title_id}/note",
        data={"note": "а" * (NOTE_MAX_LENGTH + 1)},
        headers=await csrf_headers(client),
    )

    assert response.status_code == 400
    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.note is None


async def test_note_for_unknown_title(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.post(
        "/list/999999/note", data={"note": "Привіт"}, headers=await csrf_headers(client)
    )

    assert response.status_code == 404


async def test_removing_from_list_resets_note_block(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.COMPLETED)
    headers = await csrf_headers(client)
    await client.post(f"/list/{title_id}/note", data={"note": "Шедевр"}, headers=headers)

    response = await client.delete(f"/list/{title_id}?view=title", headers=headers)

    assert f'id="note-{title_id}" hx-swap-oob="true"' in response.text
    assert "Шедевр" not in response.text


async def test_notes_are_private(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session, "first")
    title_id = await create_title(db_session)
    await client.post(
        f"/list/{title_id}/note", data={"note": "Секрет"}, headers=await csrf_headers(client)
    )
    await client.post("/logout", data={"csrf_token": (await csrf_headers(client))["X-CSRF-Token"]})
    await login(client, db_session, "second")

    assert "Секрет" not in (await client.get(f"/titles/{title_id}")).text


async def test_note_requires_csrf(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)
    title_id = await create_title(db_session)

    response = await client.post(f"/list/{title_id}/note", data={"note": "Без токена"})

    assert response.status_code == 403
