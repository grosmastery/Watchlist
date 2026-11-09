import asyncio
import logging

from watchlist.config import configure_logging, get_settings
from watchlist.db import get_engine, get_sessionmaker
from watchlist.integrations.dependencies import build_tmdb_client
from watchlist.integrations.tmdb import TMDBClient
from watchlist.tasks.sync import sync_titles

logger = logging.getLogger(__name__)


async def run_once(client: TMDBClient) -> None:
    if not client.api_key:
        logger.warning("TMDB_API_KEY is not set, skipping sync")
        return
    async with get_sessionmaker()() as session:
        result = await sync_titles(session, client)
    logger.info(
        "Sync finished: %d candidates, %d updated, %d failed",
        result.candidates,
        result.updated,
        result.failed,
    )


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    interval = settings.sync_interval_hours * 3600
    async with build_tmdb_client(settings) as client:
        try:
            while True:
                try:
                    await run_once(client)
                except Exception:
                    logger.exception("Sync run failed")
                await asyncio.sleep(interval)
        finally:
            await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
