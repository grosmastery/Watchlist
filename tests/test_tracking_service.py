import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, create_user
from watchlist.tracking.models import ListStatus, UserTitle
from watchlist.tracking.service import (
    EntryNotFoundError,
    InvalidRatingError,
    TitleNotFoundError,
    add_to_list,
    count_by_status,
    get_entry,
    list_entries,
    remove_from_list,
    update_entry,
)


async def test_add_defaults_to_planned(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    title_id = await create_title(db_session)

    entry = await add_to_list(db_session, user_id, title_id)

    assert entry.status is ListStatus.PLANNED
    assert entry.rating is None
    assert entry.added_at is not None


async def test_add_twice_keeps_single_entry(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    title_id = await create_title(db_session)

    first = await add_to_list(db_session, user_id, title_id, ListStatus.WATCHING)
    second = await add_to_list(db_session, user_id, title_id, ListStatus.DROPPED)

    assert first.id == second.id
    assert second.status is ListStatus.WATCHING
    assert len(await list_entries(db_session, user_id)) == 1


async def test_add_unknown_title(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)

    with pytest.raises(TitleNotFoundError):
        await add_to_list(db_session, user_id, 999_999)


async def test_update_status_and_rating(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)

    entry = await update_entry(db_session, user_id, title_id, status=ListStatus.COMPLETED, rating=9)

    assert entry.status is ListStatus.COMPLETED
    assert entry.rating == 9


async def test_update_only_rating_keeps_status(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id, ListStatus.WATCHING)

    await update_entry(db_session, user_id, title_id, rating=7)
    entry = await update_entry(db_session, user_id, title_id, rating=None)

    assert entry.status is ListStatus.WATCHING
    assert entry.rating is None


@pytest.mark.parametrize("rating", [0, 11, -1])
async def test_update_rejects_rating_out_of_range(db_session: AsyncSession, rating: int) -> None:
    user_id = await create_user(db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)

    with pytest.raises(InvalidRatingError):
        await update_entry(db_session, user_id, title_id, rating=rating)


async def test_database_enforces_rating_range(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    title_id = await create_title(db_session)
    db_session.add(
        UserTitle(user_id=user_id, title_id=title_id, status=ListStatus.PLANNED, rating=42)
    )

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()


async def test_update_missing_entry(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    title_id = await create_title(db_session)

    with pytest.raises(EntryNotFoundError):
        await update_entry(db_session, user_id, title_id, rating=5)


async def test_remove(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    title_id = await create_title(db_session)
    await add_to_list(db_session, user_id, title_id)

    assert await remove_from_list(db_session, user_id, title_id) is True
    assert await remove_from_list(db_session, user_id, title_id) is False
    assert await get_entry(db_session, user_id, title_id) is None


async def test_list_filter_and_counts(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    watching = await create_title(db_session, "A", tmdb_id=1)
    planned = await create_title(db_session, "B", tmdb_id=2)
    other = await create_title(db_session, "C", tmdb_id=3)
    await add_to_list(db_session, user_id, watching, ListStatus.WATCHING)
    await add_to_list(db_session, user_id, planned, ListStatus.PLANNED)
    await add_to_list(db_session, user_id, other, ListStatus.PLANNED)

    filtered = await list_entries(db_session, user_id, ListStatus.PLANNED)
    counts = await count_by_status(db_session, user_id)

    assert {entry.title_id for entry in filtered} == {planned, other}
    assert all(entry.title.name for entry in filtered)
    assert counts == {
        ListStatus.WATCHING: 1,
        ListStatus.PLANNED: 2,
        ListStatus.COMPLETED: 0,
        ListStatus.DROPPED: 0,
    }


async def test_lists_are_isolated_between_users(db_session: AsyncSession) -> None:
    alice = await create_user(db_session, "alice")
    bob = await create_user(db_session, "bob")
    title_id = await create_title(db_session)
    await add_to_list(db_session, alice, title_id, ListStatus.WATCHING)

    assert await list_entries(db_session, bob) == []
    assert await remove_from_list(db_session, bob, title_id) is False
    with pytest.raises(EntryNotFoundError):
        await update_entry(db_session, bob, title_id, status=ListStatus.DROPPED)

    entry = await get_entry(db_session, alice, title_id)
    assert entry is not None
    assert entry.status is ListStatus.WATCHING
