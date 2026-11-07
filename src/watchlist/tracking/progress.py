from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, and_, delete, false, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from watchlist.catalog.models import Episode, Season, Title, TitleType
from watchlist.tracking.models import ListStatus, UserTitle, WatchedEpisode
from watchlist.tracking.service import get_entry

FINISHED_TITLE_STATUSES = frozenset({"Ended", "Canceled"})


class EpisodeNotFoundError(Exception):
    pass


class SeasonNotFoundError(Exception):
    pass


class EpisodeNotAiredError(Exception):
    pass


def utc_today() -> date:
    return datetime.now(UTC).date()


def is_aired(air_date: date | None, today: date) -> bool:
    return air_date is not None and air_date <= today


@dataclass(frozen=True, slots=True)
class Progress:
    watched: int
    aired: int
    total: int

    @property
    def percent(self) -> int:
        if self.aired == 0:
            return 0
        return min(100, round(self.watched * 100 / self.aired))

    @property
    def is_complete(self) -> bool:
        return self.total > 0 and self.watched >= self.total


@dataclass(frozen=True, slots=True)
class NextEpisode:
    id: int
    season_number: int
    number: int
    name: str
    air_date: date | None

    @property
    def code(self) -> str:
        return f"S{self.season_number:02d}E{self.number:02d}"


@dataclass(frozen=True, slots=True)
class UpNextItem:
    title: Title
    next_episode: NextEpisode | None
    progress: Progress

    @property
    def next_air_date(self) -> date:
        if self.next_episode is not None and self.next_episode.air_date is not None:
            return self.next_episode.air_date
        return date.max

    def is_available(self, today: date) -> bool:
        return self.next_episode is not None and is_aired(self.next_episode.air_date, today)

    def is_upcoming(self, today: date) -> bool:
        return (
            self.next_episode is not None
            and self.next_episode.air_date is not None
            and self.next_episode.air_date > today
        )


async def get_episode(session: AsyncSession, episode_id: int) -> Episode:
    episode = await session.scalar(
        select(Episode)
        .where(Episode.id == episode_id)
        .options(joinedload(Episode.season).joinedload(Season.title))
    )
    if episode is None:
        raise EpisodeNotFoundError(episode_id)
    return episode


async def get_season(session: AsyncSession, season_id: int) -> Season:
    season = await session.scalar(
        select(Season)
        .where(Season.id == season_id)
        .options(selectinload(Season.episodes), joinedload(Season.title))
        .execution_options(populate_existing=True)
    )
    if season is None:
        raise SeasonNotFoundError(season_id)
    return season


async def watched_episode_ids(
    session: AsyncSession, user_id: int, episode_ids: Sequence[int]
) -> set[int]:
    if not episode_ids:
        return set()
    rows = await session.scalars(
        select(WatchedEpisode.episode_id).where(
            WatchedEpisode.user_id == user_id, WatchedEpisode.episode_id.in_(episode_ids)
        )
    )
    return set(rows)


async def watched_ids_for_title(session: AsyncSession, user_id: int, title_id: int) -> set[int]:
    rows = await session.scalars(
        select(WatchedEpisode.episode_id)
        .join(Episode, Episode.id == WatchedEpisode.episode_id)
        .join(Season, Season.id == Episode.season_id)
        .where(WatchedEpisode.user_id == user_id, Season.title_id == title_id)
    )
    return set(rows)


async def _insert_watched(session: AsyncSession, user_id: int, episode_ids: Sequence[int]) -> None:
    if not episode_ids:
        return
    await session.execute(
        insert(WatchedEpisode)
        .values([{"user_id": user_id, "episode_id": episode_id} for episode_id in episode_ids])
        .on_conflict_do_nothing(index_elements=[WatchedEpisode.user_id, WatchedEpisode.episode_id])
    )


async def _delete_watched(session: AsyncSession, user_id: int, episode_ids: Sequence[int]) -> None:
    if not episode_ids:
        return
    await session.execute(
        delete(WatchedEpisode).where(
            WatchedEpisode.user_id == user_id, WatchedEpisode.episode_id.in_(episode_ids)
        )
    )


async def mark_episode(
    session: AsyncSession, user_id: int, episode_id: int, today: date | None = None
) -> Episode:
    today = today or utc_today()
    episode = await get_episode(session, episode_id)
    if not is_aired(episode.air_date, today):
        raise EpisodeNotAiredError(episode_id)
    await _insert_watched(session, user_id, [episode.id])
    await sync_list_status(session, user_id, episode.season.title, today)
    return episode


async def unmark_episode(
    session: AsyncSession, user_id: int, episode_id: int, today: date | None = None
) -> Episode:
    today = today or utc_today()
    episode = await get_episode(session, episode_id)
    await _delete_watched(session, user_id, [episode.id])
    await sync_list_status(session, user_id, episode.season.title, today)
    return episode


async def mark_season(
    session: AsyncSession, user_id: int, season_id: int, today: date | None = None
) -> Season:
    today = today or utc_today()
    season = await get_season(session, season_id)
    aired = [episode.id for episode in season.episodes if is_aired(episode.air_date, today)]
    if not aired:
        raise EpisodeNotAiredError(season_id)
    await _insert_watched(session, user_id, aired)
    await sync_list_status(session, user_id, season.title, today)
    return season


async def unmark_season(
    session: AsyncSession, user_id: int, season_id: int, today: date | None = None
) -> Season:
    today = today or utc_today()
    season = await get_season(session, season_id)
    await _delete_watched(session, user_id, [episode.id for episode in season.episodes])
    await sync_list_status(session, user_id, season.title, today)
    return season


async def sync_list_status(
    session: AsyncSession, user_id: int, title: Title, today: date | None = None
) -> UserTitle | None:
    progress = await title_progress(session, user_id, title.id, today)
    entry = await get_entry(session, user_id, title.id)
    finished = title.status in FINISHED_TITLE_STATUSES and progress.is_complete
    if entry is None:
        if progress.watched == 0:
            return None
        entry = UserTitle(
            user_id=user_id,
            title_id=title.id,
            status=ListStatus.COMPLETED if finished else ListStatus.WATCHING,
        )
        session.add(entry)
    elif finished:
        entry.status = ListStatus.COMPLETED
    elif progress.watched > 0 and entry.status is not ListStatus.WATCHING:
        entry.status = ListStatus.WATCHING
    await session.flush()
    await session.refresh(entry)
    return entry


def _ordered_episodes(user_id: int) -> Select[Any]:
    return (
        select(
            Season.title_id.label("title_id"),
            Episode.id.label("episode_id"),
            Season.number.label("season_number"),
            Episode.number.label("episode_number"),
            Episode.name.label("episode_name"),
            Episode.air_date.label("air_date"),
            WatchedEpisode.episode_id.is_not(None).label("watched"),
            func.row_number()
            .over(partition_by=Season.title_id, order_by=(Season.number, Episode.number))
            .label("position"),
        )
        .select_from(Episode)
        .join(Season, Season.id == Episode.season_id)
        .outerjoin(
            WatchedEpisode,
            and_(WatchedEpisode.episode_id == Episode.id, WatchedEpisode.user_id == user_id),
        )
        .where(Season.number > 0)
    )


def up_next_statement(user_id: int, today: date, title_id: int | None = None) -> Select[Any]:
    ordered_query = _ordered_episodes(user_id)
    if title_id is None:
        ordered_query = ordered_query.join(
            UserTitle,
            and_(
                UserTitle.title_id == Season.title_id,
                UserTitle.user_id == user_id,
                UserTitle.status == ListStatus.WATCHING,
            ),
        )
    else:
        ordered_query = ordered_query.where(Season.title_id == title_id)
    ordered = ordered_query.cte("ordered")

    stats = (
        select(
            ordered.c.title_id,
            func.coalesce(func.max(ordered.c.position).filter(ordered.c.watched), 0).label(
                "last_position"
            ),
            func.count().filter(ordered.c.watched).label("watched_count"),
            func.count().filter(ordered.c.air_date <= today).label("aired_count"),
            func.count().label("total_count"),
        )
        .group_by(ordered.c.title_id)
        .cte("stats")
    )

    next_position = (
        select(ordered.c.title_id, func.min(ordered.c.position).label("position"))
        .join(stats, stats.c.title_id == ordered.c.title_id)
        .where(ordered.c.watched == false(), ordered.c.position > stats.c.last_position)
        .group_by(ordered.c.title_id)
        .cte("next_position")
    )

    statement = select(
        Title,
        func.coalesce(stats.c.watched_count, 0),
        func.coalesce(stats.c.aired_count, 0),
        func.coalesce(stats.c.total_count, 0),
        ordered.c.episode_id,
        ordered.c.season_number,
        ordered.c.episode_number,
        ordered.c.episode_name,
        ordered.c.air_date,
    )
    if title_id is None:
        statement = (
            statement.select_from(UserTitle)
            .join(Title, Title.id == UserTitle.title_id)
            .where(
                UserTitle.user_id == user_id,
                UserTitle.status == ListStatus.WATCHING,
                Title.type == TitleType.SERIES,
            )
            .order_by(UserTitle.updated_at.desc(), Title.id)
        )
    else:
        statement = statement.select_from(Title).where(Title.id == title_id)
    return (
        statement.outerjoin(stats, stats.c.title_id == Title.id)
        .outerjoin(next_position, next_position.c.title_id == Title.id)
        .outerjoin(
            ordered,
            and_(
                ordered.c.title_id == next_position.c.title_id,
                ordered.c.position == next_position.c.position,
            ),
        )
    )


async def up_next(
    session: AsyncSession, user_id: int, today: date | None = None, title_id: int | None = None
) -> list[UpNextItem]:
    today = today or utc_today()
    rows = await session.execute(up_next_statement(user_id, today, title_id))
    items: list[UpNextItem] = []
    for row in rows:
        (title, watched, aired, total, episode_id, season_number, number, name, air_date) = row
        next_episode = (
            NextEpisode(episode_id, season_number, number, name, air_date)
            if episode_id is not None
            else None
        )
        items.append(UpNextItem(title, next_episode, Progress(watched, aired, total)))
    return items


async def title_progress(
    session: AsyncSession, user_id: int, title_id: int, today: date | None = None
) -> Progress:
    items = await up_next(session, user_id, today, title_id)
    return items[0].progress if items else Progress(0, 0, 0)


async def up_next_for_title(
    session: AsyncSession, user_id: int, title_id: int, today: date | None = None
) -> UpNextItem | None:
    items = await up_next(session, user_id, today, title_id)
    return items[0] if items else None
