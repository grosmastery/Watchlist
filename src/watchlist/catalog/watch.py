from dataclasses import dataclass

from watchlist.integrations.tmdb import TMDBClient
from watchlist.integrations.tmdb_schemas import MediaType, WatchOptions, WatchProvider

TMDB_SITE = "https://www.themoviedb.org/"
GROUPS = (
    ("Підписка", ("flatrate",)),
    ("Безкоштовно", ("free", "ads")),
    ("Оренда", ("rent",)),
    ("Купівля", ("buy",)),
)


@dataclass(frozen=True, slots=True)
class ProviderGroup:
    label: str
    providers: tuple[WatchProvider, ...]


@dataclass(frozen=True, slots=True)
class WatchInfo:
    link: str
    groups: tuple[ProviderGroup, ...]


def group_providers(options: WatchOptions) -> tuple[ProviderGroup, ...]:
    groups: list[ProviderGroup] = []
    for label, kinds in GROUPS:
        found: dict[int, WatchProvider] = {}
        for kind in kinds:
            for provider in getattr(options, kind):
                found.setdefault(provider.provider_id, provider)
        if found:
            ordered = sorted(found.values(), key=lambda provider: provider.display_priority)
            groups.append(ProviderGroup(label, tuple(ordered)))
    return tuple(groups)


async def find_watch_info(
    client: TMDBClient, media_type: MediaType, tmdb_id: int
) -> WatchInfo | None:
    options = await client.get_watch_options(media_type, tmdb_id)
    if options is None:
        return None
    groups = group_providers(options)
    if not groups:
        return None
    link = options.link
    if not link.startswith(TMDB_SITE):
        link = f"{TMDB_SITE}{media_type}/{tmdb_id}/watch"
    return WatchInfo(link, groups)
