from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title
from tests.test_tracking_routes import csrf_headers, login
from watchlist.tracking.models import ListStatus
from watchlist.tracking.service import add_to_list, get_entry


async def test_title_page_shows_star_rating(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.WATCHING)
    await client.post(
        f"/list/{title_id}/rating", data={"rating": "7"}, headers=await csrf_headers(client)
    )

    response = await client.get(f"/titles/{title_id}")

    assert response.text.count("star star-on") == 7
    assert "7/10" in response.text
    assert 'name="rating"' not in response.text


async def test_rating_updates_entry_and_controls(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.WATCHING)

    response = await client.post(
        f"/list/{title_id}/rating", data={"rating": "9"}, headers=await csrf_headers(client)
    )

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "list-changed"
    assert f'id="rating-{title_id}"' in response.text
    assert f'id="entry-{title_id}" hx-swap-oob="true"' in response.text
    assert '{"rating": ""}' in response.text
    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.rating == 9
    assert entry.status is ListStatus.WATCHING


async def test_rating_unlisted_title_adds_it_as_completed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)

    await client.post(
        f"/list/{title_id}/rating", data={"rating": "8"}, headers=await csrf_headers(client)
    )

    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.status is ListStatus.COMPLETED
    assert entry.rating == 8


async def test_clearing_rating_keeps_entry(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    headers = await csrf_headers(client)
    await client.post(f"/list/{title_id}/rating", data={"rating": "8"}, headers=headers)

    response = await client.post(f"/list/{title_id}/rating", data={"rating": ""}, headers=headers)

    assert "star-on" not in response.text
    entry = await get_entry(db_session, user_id, title_id)
    assert entry is not None
    assert entry.rating is None


async def test_clearing_rating_of_unlisted_title_adds_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)

    response = await client.post(
        f"/list/{title_id}/rating", data={"rating": ""}, headers=await csrf_headers(client)
    )

    assert response.status_code == 200
    assert "Оцінка додасть тайтл у список" in response.text
    assert await get_entry(db_session, user_id, title_id) is None


async def test_rating_out_of_range_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    headers = await csrf_headers(client)

    too_high = await client.post(f"/list/{title_id}/rating", data={"rating": "11"}, headers=headers)
    garbage = await client.post(f"/list/{title_id}/rating", data={"rating": "abc"}, headers=headers)

    assert too_high.status_code == 400
    assert garbage.status_code == 400
    assert await get_entry(db_session, user_id, title_id) is None


async def test_rating_missing_title(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.post(
        "/list/999999/rating", data={"rating": "5"}, headers=await csrf_headers(client)
    )

    assert response.status_code == 404


async def test_rating_requires_csrf(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)
    title_id = await create_title(db_session)

    response = await client.post(f"/list/{title_id}/rating", data={"rating": "5"})

    assert response.status_code == 403


async def test_status_change_on_title_page_refreshes_rating(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.WATCHING)

    on_title = await client.delete(
        f"/list/{title_id}?view=title", headers=await csrf_headers(client)
    )

    assert f'id="rating-{title_id}" hx-swap-oob="true"' in on_title.text


async def test_list_view_keeps_rating_select(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.WATCHING)

    response = await client.get("/list")

    assert 'name="rating"' in response.text
    assert f'id="rating-{title_id}"' not in response.text
