from dataclasses import dataclass

from watchlist.integrations.tmdb import TMDBClient
from watchlist.integrations.tmdb_schemas import MediaType

CAST_LIMIT = 20


@dataclass(frozen=True, slots=True)
class Actor:
    tmdb_id: int
    name: str
    role: str
    photo_path: str | None

    @property
    def url(self) -> str:
        return f"https://www.themoviedb.org/person/{self.tmdb_id}"


async def find_cast(client: TMDBClient, media_type: MediaType, tmdb_id: int) -> list[Actor]:
    members = await client.get_cast(media_type, tmdb_id)
    return [
        Actor(member.id, member.name, member.role, member.profile_path)
        for member in members[:CAST_LIMIT]
    ]
