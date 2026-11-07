from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import fetch_csrf_token, register_user
from tests.factories import AIRED, FAR_FUTURE, create_series
from watchlist.auth.models import User
from watchlist.tracking.models import ListStatus
from watchlist.tracking.progress import watched_ids_for_title
from watchlist.tracking.service import add_to_list, get_entry

HTMX = {"HX-Request": "true"}
THREE_AIRED = [AIRED, AIRED, AIRED]


async def login(client: AsyncClient, session: AsyncSession, username: str = "viewer") -> int:
    await register_user(client, email=f"{username}@example.com", username=username)
    user_id = await session.scalar(select(User.id).where(User.username == username))
    assert user_id is not None
    return user_id


async def headers(client: AsyncClient) -> dict[str, str]:
    return {**HTMX, "X-CSRF-Token": await fetch_csrf_token(client, "/list")}


async def test_up_next_requires_login(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 303


async def test_up_next_empty_state(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)

    response = await client.get("/")

    assert "серіалів зі статусом «Дивлюсь»" in response.text


async def test_marking_episode_updates_up_next(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    series = await create_series(db_session, [THREE_AIRED], name="Мій серіал")
    await add_to_list(db_session, user_id, series.title_id, ListStatus.WATCHING)

    before = await client.get("/")
    response = await client.post(
        f"/episodes/{series.episode(1, 1)}/watched?view=next", headers=await headers(client)
    )
    after = await client.get("/")

    assert "S01E01" in before.text
    assert "Можна дивитись" in before.text
    assert response.status_code == 200
    assert f'id="next-{series.title_id}"' in response.text
    assert "S01E02" in response.text
    assert "S01E02" in after.text
    assert "S01E01" not in after.text


async def test_future_episode_goes_to_upcoming_section(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    series = await create_series(db_session, [[AIRED, FAR_FUTURE]])
    await add_to_list(db_session, user_id, series.title_id, ListStatus.WATCHING)

    await client.post(
        f"/episodes/{series.episode(1, 1)}/watched?view=next", headers=await headers(client)
    )
    page = await client.get("/")

    assert "Скоро вийде" in page.text
    assert "Можна дивитись" not in page.text
    assert "01.01.2999" in page.text
    assert f'hx-post="/episodes/{series.episode(1, 2)}/watched' not in page.text


async def test_cannot_mark_future_episode(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    series = await create_series(db_session, [[FAR_FUTURE]])

    response = await client.post(
        f"/episodes/{series.episode(1, 1)}/watched", headers=await headers(client)
    )

    assert response.status_code == 400
    assert await watched_ids_for_title(db_session, user_id, series.title_id) == set()


async def test_mark_episode_on_title_page(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    series = await create_series(db_session, [THREE_AIRED])

    response = await client.post(
        f"/episodes/{series.episode(1, 1)}/watched?view=title", headers=await headers(client)
    )

    assert response.status_code == 200
    assert f'id="season-{series.season(1)}"' in response.text
    assert f'id="progress-{series.title_id}" hx-swap-oob="true"' in response.text
    assert f'id="entry-{series.title_id}" hx-swap-oob="true"' in response.text
    assert "1/3" in response.text
    assert "Далі:" in response.text
    entry = await get_entry(db_session, user_id, series.title_id)
    assert entry is not None
    assert entry.status is ListStatus.WATCHING


async def test_unmark_episode(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    series = await create_series(db_session, [THREE_AIRED])
    episode_id = series.episode(1, 2)
    request_headers = await headers(client)
    await client.post(f"/episodes/{episode_id}/watched", headers=request_headers)

    response = await client.delete(f"/episodes/{episode_id}/watched", headers=request_headers)

    assert response.status_code == 200
    assert await watched_ids_for_title(db_session, user_id, series.title_id) == set()


async def test_mark_and_unmark_season(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await login(client, db_session)
    series = await create_series(db_session, [[AIRED, AIRED, FAR_FUTURE]])
    request_headers = await headers(client)

    marked = await client.post(f"/seasons/{series.season(1)}/watched", headers=request_headers)
    watched = await watched_ids_for_title(db_session, user_id, series.title_id)
    unmarked = await client.delete(f"/seasons/{series.season(1)}/watched", headers=request_headers)

    assert marked.status_code == 200
    assert watched == {series.episode(1, 1), series.episode(1, 2)}
    assert "Зняти позначки з сезону" in marked.text
    assert unmarked.status_code == 200
    assert "Позначити сезон" in unmarked.text
    assert await watched_ids_for_title(db_session, user_id, series.title_id) == set()


async def test_finished_series_is_completed_after_last_season(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await login(client, db_session)
    series = await create_series(db_session, [THREE_AIRED], status="Ended")

    response = await client.post(
        f"/seasons/{series.season(1)}/watched", headers=await headers(client)
    )

    entry = await get_entry(db_session, user_id, series.title_id)
    assert entry is not None
    assert entry.status is ListStatus.COMPLETED
    assert '<option value="completed" selected>' in response.text
    assert "Усі серії переглянуто" in response.text


async def test_unknown_episode_and_season(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)
    request_headers = await headers(client)

    episode = await client.post("/episodes/999999/watched", headers=request_headers)
    season = await client.post("/seasons/999999/watched", headers=request_headers)

    assert episode.status_code == 404
    assert season.status_code == 404


async def test_marking_requires_csrf(client: AsyncClient, db_session: AsyncSession) -> None:
    await login(client, db_session)
    series = await create_series(db_session, [THREE_AIRED])

    response = await client.post(f"/episodes/{series.episode(1, 1)}/watched", headers=HTMX)

    assert response.status_code == 403


async def test_progress_is_private(client: AsyncClient, db_session: AsyncSession) -> None:
    owner_id = await login(client, db_session, "owner")
    series = await create_series(db_session, [THREE_AIRED], name="Чужий серіал")
    await client.post(f"/seasons/{series.season(1)}/watched", headers=await headers(client))
    await client.post("/logout", headers=await headers(client))

    await login(client, db_session, "guest")
    page = await client.get(f"/titles/{series.title_id}")
    up_next_page = await client.get("/")

    assert "0/3" in page.text
    assert "Чужий серіал" not in up_next_page.text
    assert len(await watched_ids_for_title(db_session, owner_id, series.title_id)) == 3


async def test_title_page_shows_progress_and_controls(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login(client, db_session)
    series = await create_series(db_session, [[AIRED, FAR_FUTURE]])
    await client.post(f"/episodes/{series.episode(1, 1)}/watched", headers=await headers(client))

    page = await client.get(f"/titles/{series.title_id}")

    assert "Переглянуто 1 з 1" in page.text
    assert "усього 2" in page.text
    assert f'hx-delete="/episodes/{series.episode(1, 1)}/watched' in page.text
    assert "Серія ще не вийшла" in page.text
    assert f'hx-post="/episodes/{series.episode(1, 2)}/watched' not in page.text
