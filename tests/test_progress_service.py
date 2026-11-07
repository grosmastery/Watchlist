import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_title, create_user
from tests.factories import AIRED, FUTURE, TODAY, create_series
from watchlist.catalog.models import Episode, Season
from watchlist.tracking.models import ListStatus
from watchlist.tracking.progress import (
    EpisodeNotAiredError,
    EpisodeNotFoundError,
    mark_episode,
    mark_season,
    title_progress,
    unmark_episode,
    unmark_season,
    up_next,
    up_next_for_title,
    watched_ids_for_title,
)
from watchlist.tracking.service import add_to_list, get_entry

THREE_AIRED = [AIRED, AIRED, AIRED]


async def next_code(session: AsyncSession, user_id: int, title_id: int) -> str | None:
    item = await up_next_for_title(session, user_id, title_id, TODAY)
    assert item is not None
    return item.next_episode.code if item.next_episode else None


async def test_next_is_first_episode_when_nothing_watched(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED, THREE_AIRED])

    assert await next_code(db_session, user_id, series.title_id) == "S01E01"


async def test_next_moves_forward_and_crosses_seasons(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED, THREE_AIRED])

    await mark_episode(db_session, user_id, series.episode(1, 1), TODAY)
    assert await next_code(db_session, user_id, series.title_id) == "S01E02"

    await mark_episode(db_session, user_id, series.episode(1, 2), TODAY)
    await mark_episode(db_session, user_id, series.episode(1, 3), TODAY)
    assert await next_code(db_session, user_id, series.title_id) == "S02E01"


async def test_next_follows_last_watched_not_first_gap(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED, THREE_AIRED])

    await mark_episode(db_session, user_id, series.episode(2, 1), TODAY)

    assert await next_code(db_session, user_id, series.title_id) == "S02E02"


async def test_future_episode_is_not_available(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [[AIRED, FUTURE]])
    await mark_episode(db_session, user_id, series.episode(1, 1), TODAY)

    item = await up_next_for_title(db_session, user_id, series.title_id, TODAY)

    assert item is not None
    assert item.next_episode is not None
    assert item.next_episode.code == "S01E02"
    assert item.next_episode.air_date == FUTURE
    assert not item.is_available(TODAY)
    assert item.is_upcoming(TODAY)
    assert item.progress.aired == 1


async def test_episode_without_date_is_neither_available_nor_upcoming(
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [[AIRED, None]])
    await mark_episode(db_session, user_id, series.episode(1, 1), TODAY)

    item = await up_next_for_title(db_session, user_id, series.title_id, TODAY)

    assert item is not None
    assert not item.is_available(TODAY)
    assert not item.is_upcoming(TODAY)


async def test_cannot_mark_future_or_undated_episode(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [[FUTURE, None]])

    for episode_id in series.episode_ids[0]:
        with pytest.raises(EpisodeNotAiredError):
            await mark_episode(db_session, user_id, episode_id, TODAY)

    assert await watched_ids_for_title(db_session, user_id, series.title_id) == set()


async def test_mark_unknown_episode(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)

    with pytest.raises(EpisodeNotFoundError):
        await mark_episode(db_session, user_id, 999_999, TODAY)


async def test_marking_twice_is_idempotent(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED])

    await mark_episode(db_session, user_id, series.episode(1, 1), TODAY)
    await mark_episode(db_session, user_id, series.episode(1, 1), TODAY)

    progress = await title_progress(db_session, user_id, series.title_id, TODAY)
    assert progress.watched == 1


async def test_mark_season_skips_unaired(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [[AIRED, AIRED, FUTURE]])

    await mark_season(db_session, user_id, series.season(1), TODAY)

    watched = await watched_ids_for_title(db_session, user_id, series.title_id)
    assert watched == {series.episode(1, 1), series.episode(1, 2)}
    assert await next_code(db_session, user_id, series.title_id) == "S01E03"


async def test_mark_season_without_aired_episodes(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [[FUTURE]])

    with pytest.raises(EpisodeNotAiredError):
        await mark_season(db_session, user_id, series.season(1), TODAY)


async def test_unmark_episode_and_season(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED, THREE_AIRED])
    await mark_season(db_session, user_id, series.season(1), TODAY)
    await mark_season(db_session, user_id, series.season(2), TODAY)

    await unmark_episode(db_session, user_id, series.episode(2, 3), TODAY)
    assert await next_code(db_session, user_id, series.title_id) == "S02E03"

    await unmark_season(db_session, user_id, series.season(2), TODAY)
    assert await next_code(db_session, user_id, series.title_id) == "S02E01"
    progress = await title_progress(db_session, user_id, series.title_id, TODAY)
    assert progress.watched == 3


async def test_marking_adds_series_to_list_as_watching(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED])

    await mark_episode(db_session, user_id, series.episode(1, 1), TODAY)

    entry = await get_entry(db_session, user_id, series.title_id)
    assert entry is not None
    assert entry.status is ListStatus.WATCHING


@pytest.mark.parametrize("initial", [ListStatus.PLANNED, ListStatus.DROPPED])
async def test_marking_moves_entry_to_watching(
    db_session: AsyncSession, initial: ListStatus
) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED])
    await add_to_list(db_session, user_id, series.title_id, initial)

    await mark_episode(db_session, user_id, series.episode(1, 1), TODAY)

    entry = await get_entry(db_session, user_id, series.title_id)
    assert entry is not None
    assert entry.status is ListStatus.WATCHING


async def test_finished_series_becomes_completed_and_back(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED], status="Ended")

    await mark_season(db_session, user_id, series.season(1), TODAY)
    entry = await get_entry(db_session, user_id, series.title_id)
    assert entry is not None
    assert entry.status is ListStatus.COMPLETED

    await unmark_episode(db_session, user_id, series.episode(1, 3), TODAY)
    entry = await get_entry(db_session, user_id, series.title_id)
    assert entry is not None
    assert entry.status is ListStatus.WATCHING


async def test_returning_series_stays_watching_when_caught_up(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED])

    await mark_season(db_session, user_id, series.season(1), TODAY)

    entry = await get_entry(db_session, user_id, series.title_id)
    item = await up_next_for_title(db_session, user_id, series.title_id, TODAY)
    assert entry is not None
    assert entry.status is ListStatus.WATCHING
    assert item is not None
    assert item.next_episode is None
    assert item.progress.percent == 100


async def test_up_next_lists_only_watching_series(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    watching = await create_series(db_session, [THREE_AIRED], name="Дивлюсь", tmdb_id=1)
    planned = await create_series(db_session, [THREE_AIRED], name="Колись", tmdb_id=2)
    movie_id = await create_title(db_session, "Фільм", tmdb_id=3, title_type="movie")
    await add_to_list(db_session, user_id, watching.title_id, ListStatus.WATCHING)
    await add_to_list(db_session, user_id, planned.title_id, ListStatus.PLANNED)
    await add_to_list(db_session, user_id, movie_id, ListStatus.WATCHING)

    items = await up_next(db_session, user_id, TODAY)

    assert [item.title.name for item in items] == ["Дивлюсь"]


async def test_up_next_includes_series_without_episodes(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [])
    await add_to_list(db_session, user_id, series.title_id, ListStatus.WATCHING)

    items = await up_next(db_session, user_id, TODAY)

    assert len(items) == 1
    assert items[0].next_episode is None
    assert items[0].progress.total == 0


async def test_progress_is_per_user(db_session: AsyncSession) -> None:
    alice = await create_user(db_session, "alice")
    bob = await create_user(db_session, "bob")
    series = await create_series(db_session, [THREE_AIRED])
    await mark_season(db_session, alice, series.season(1), TODAY)

    assert (await title_progress(db_session, bob, series.title_id, TODAY)).watched == 0
    assert await next_code(db_session, bob, series.title_id) == "S01E01"


async def test_specials_are_ignored(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    series = await create_series(db_session, [THREE_AIRED])
    specials = Season(title_id=series.title_id, number=0, name="Спецвипуски")
    db_session.add(specials)
    await db_session.flush()
    db_session.add(Episode(season_id=specials.id, number=1, name="Спецвипуск", air_date=AIRED))
    await db_session.flush()

    progress = await title_progress(db_session, user_id, series.title_id, TODAY)

    assert progress.total == 3
    assert await next_code(db_session, user_id, series.title_id) == "S01E01"


async def test_up_next_for_many_series_in_one_query(db_session: AsyncSession) -> None:
    user_id = await create_user(db_session)
    first = await create_series(db_session, [THREE_AIRED, THREE_AIRED], name="Перший", tmdb_id=1)
    second = await create_series(db_session, [[AIRED, FUTURE]], name="Другий", tmdb_id=2)
    await mark_episode(db_session, user_id, first.episode(1, 3), TODAY)
    await mark_episode(db_session, user_id, second.episode(1, 1), TODAY)

    items = {item.title.name: item for item in await up_next(db_session, user_id, TODAY)}

    first_next = items["Перший"].next_episode
    second_next = items["Другий"].next_episode
    assert first_next is not None
    assert first_next.code == "S02E01"
    assert items["Перший"].is_available(TODAY)
    assert second_next is not None
    assert second_next.code == "S01E02"
    assert items["Другий"].is_upcoming(TODAY)
