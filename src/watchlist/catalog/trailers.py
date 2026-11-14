import re
from collections.abc import Iterable

from watchlist.integrations.tmdb import TMDBClient
from watchlist.integrations.tmdb_schemas import MediaType, Video

TRAILER_TYPES = ("Trailer", "Teaser")
YOUTUBE_KEY = re.compile(r"^[\w-]{6,20}$")


def pick_trailer(videos: Iterable[Video], language: str) -> str | None:
    candidates = [
        video
        for video in videos
        if video.site == "YouTube" and video.type in TRAILER_TYPES and YOUTUBE_KEY.match(video.key)
    ]
    if not candidates:
        return None
    best = min(
        candidates,
        key=lambda video: (
            video.iso_639_1 != language,
            TRAILER_TYPES.index(video.type),
            not video.official,
        ),
    )
    return best.key


async def find_trailer(client: TMDBClient, media_type: MediaType, tmdb_id: int) -> str | None:
    videos = await client.get_videos(media_type, tmdb_id)
    return pick_trailer(videos, client.language[:2])
