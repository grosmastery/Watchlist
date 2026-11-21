from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import groupby

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.catalog.models import Episode, Season, Title
from watchlist.tracking.models import ListStatus, UserTitle, WatchedEpisode

MONTH_NAMES = (
    "Січень",
    "Лютий",
    "Березень",
    "Квітень",
    "Травень",
    "Червень",
    "Липень",
    "Серпень",
    "Вересень",
    "Жовтень",
    "Листопад",
    "Грудень",
)
MONTH_GENITIVE = (
    "січня",
    "лютого",
    "березня",
    "квітня",
    "травня",
    "червня",
    "липня",
    "серпня",
    "вересня",
    "жовтня",
    "листопада",
    "грудня",
)
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")


@dataclass(frozen=True, slots=True)
class Release:
    title_id: int
    title_name: str
    poster_path: str | None
    season: int
    first: int
    last: int
    episode_name: str
    watched: bool

    @property
    def code(self) -> str:
        episodes = f"E{self.first}" if self.first == self.last else f"E{self.first}–{self.last}"
        return f"S{self.season} {episodes}"


@dataclass(slots=True)
class Day:
    date: date
    in_month: bool
    is_today: bool
    releases: list[Release] = field(default_factory=list)

    @property
    def label(self) -> str:
        weekday = WEEKDAYS[self.date.weekday()]
        return f"{weekday}, {self.date.day} {MONTH_GENITIVE[self.date.month - 1]}"


@dataclass(frozen=True, slots=True)
class CalendarMonth:
    month: date
    weeks: list[list[Day]]

    @property
    def title(self) -> str:
        return f"{MONTH_NAMES[self.month.month - 1]} {self.month.year}"

    @property
    def previous(self) -> date:
        return shift_month(self.month, -1)

    @property
    def next(self) -> date:
        return shift_month(self.month, 1)

    @property
    def release_count(self) -> int:
        return sum(len(day.releases) for week in self.weeks for day in week if day.in_month)


def shift_month(month: date, offset: int) -> date:
    index = month.year * 12 + month.month - 1 + offset
    year, zero_based = divmod(index, 12)
    return date(year, zero_based + 1, 1)


def grid_bounds(month: date) -> tuple[date, date]:
    start = month - timedelta(days=month.weekday())
    month_end = shift_month(month, 1) - timedelta(days=1)
    end = month_end + timedelta(days=6 - month_end.weekday())
    return start, end


async def releases_between(
    session: AsyncSession, user_id: int, start: date, end: date
) -> dict[date, list[Release]]:
    rows = await session.execute(
        select(
            Episode.air_date,
            Title.id,
            Title.name,
            Title.poster_path,
            Season.number,
            Episode.number,
            Episode.name,
            WatchedEpisode.episode_id.is_not(None),
        )
        .join(Season, Season.id == Episode.season_id)
        .join(Title, Title.id == Season.title_id)
        .join(UserTitle, UserTitle.title_id == Title.id)
        .outerjoin(
            WatchedEpisode,
            and_(WatchedEpisode.episode_id == Episode.id, WatchedEpisode.user_id == user_id),
        )
        .where(
            UserTitle.user_id == user_id,
            UserTitle.status != ListStatus.DROPPED,
            Season.number > 0,
            Episode.air_date.between(start, end),
        )
        .order_by(Episode.air_date, Title.name, Title.id, Season.number, Episode.number)
    )
    releases: dict[date, list[Release]] = defaultdict(list)
    for (air_date, title_id, season), group in groupby(
        rows.all(), key=lambda row: (row[0], row[1], row[4])
    ):
        episodes = list(group)
        first, last = episodes[0], episodes[-1]
        releases[air_date].append(
            Release(
                title_id=title_id,
                title_name=first[2],
                poster_path=first[3],
                season=season,
                first=first[5],
                last=last[5],
                episode_name=first[6] if len(episodes) == 1 else "",
                watched=all(row[7] for row in episodes),
            )
        )
    return releases


async def calendar_month(
    session: AsyncSession, user_id: int, month: date, today: date
) -> CalendarMonth:
    month = month.replace(day=1)
    start, end = grid_bounds(month)
    releases = await releases_between(session, user_id, start, end)
    days = [
        Day(
            date=current,
            in_month=current.month == month.month,
            is_today=current == today,
            releases=releases.get(current, []),
        )
        for current in (start + timedelta(days=offset) for offset in range((end - start).days + 1))
    ]
    weeks = [days[index : index + 7] for index in range(0, len(days), 7)]
    return CalendarMonth(month, weeks)
