import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.catalog.models import Episode, Season, Title, TitleType
from watchlist.catalog.service import import_title
from watchlist.integrations.tmdb import TMDBClient, TMDBError
from watchlist.tracking.models import UserTitle
from watchlist.tracking.progress import FINISHED_TITLE_STATUSES

logger = logging.getLogger(__name__)

DAILY_INTERVAL = timedelta(hours=20)
WEEKLY_INTERVAL = timedelta(days=7)
AIRING_SOON_WINDOW = timedelta(days=14)
CHANGES_MAX_WINDOW = timedelta(days=14)


@dataclass(frozen=True, slots=True)
class SyncCandidate:
    title_id: int
    tmdb_id: int
    name: str
    last_synced_at: datetime | None
    airing_soon: bool


@dataclass(frozen=True, slots=True)
class SyncResult:
    candidates: int
    updated: int
    failed: int


def is_due(candidate: SyncCandidate, now: datetime, changed_ids: set[int]) -> bool:
    if candidate.last_synced_at is None:
        return True
    age = now - candidate.last_synced_at
    if age >= WEEKLY_INTERVAL:
        return True
    return age >= DAILY_INTERVAL and (candidate.airing_soon or candidate.tmdb_id in changed_ids)


def depends_on_changes(candidate: SyncCandidate, now: datetime) -> bool:
    if candidate.last_synced_at is None or candidate.airing_soon:
        return False
    return DAILY_INTERVAL <= now - candidate.last_synced_at < WEEKLY_INTERVAL


def changes_window_start(candidates: list[SyncCandidate], now: datetime) -> date:
    earliest_allowed = now - CHANGES_MAX_WINDOW
    synced = [item.last_synced_at for item in candidates if item.last_synced_at is not None]
    start = max(synced, default=earliest_allowed)
    return max(start, earliest_allowed).date()


async def load_candidates(session: AsyncSession, today: date) -> list[SyncCandidate]:
    airing_soon = exists().where(
        and_(
            Season.title_id == Title.id,
            Episode.season_id == Season.id,
            Episode.air_date.between(today, today + AIRING_SOON_WINDOW),
        )
    )
    rows = await session.execute(
        select(
            Title.id,
            Title.tmdb_id,
            Title.name,
            Title.last_synced_at,
            airing_soon.label("airing_soon"),
        ).where(
            Title.type == TitleType.SERIES,
            or_(Title.status.is_(None), Title.status.not_in(FINISHED_TITLE_STATUSES)),
            exists().where(UserTitle.title_id == Title.id),
        )
    )
    return [SyncCandidate(*row) for row in rows]


async def fetch_changed_ids(
    client: TMDBClient, candidates: list[SyncCandidate], now: datetime
) -> set[int]:
    pending = [item for item in candidates if depends_on_changes(item, now)]
    if not pending:
        return set()
    try:
        return await client.get_changed_series(changes_window_start(pending, now), now.date())
    except TMDBError:
        logger.exception("Could not load the TMDB changes list")
        return set()


async def sync_titles(
    session: AsyncSession, client: TMDBClient, now: datetime | None = None
) -> SyncResult:
    now = now or datetime.now(UTC)
    candidates = await load_candidates(session, now.date())
    await session.commit()
    if not candidates:
        return SyncResult(0, 0, 0)

    changed_ids = await fetch_changed_ids(client, candidates, now)
    updated = 0
    failed = 0
    for candidate in candidates:
        if not is_due(candidate, now, changed_ids):
            continue
        try:
            await import_title(session, client, TitleType.SERIES, candidate.tmdb_id)
            await session.commit()
        except Exception:
            await session.rollback()
            failed += 1
            logger.exception("Could not sync %s (TMDB %d)", candidate.name, candidate.tmdb_id)
        else:
            updated += 1
    return SyncResult(len(candidates), updated, failed)
