import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://watchlist:watchlist@localhost:5433/watchlist_test",
)

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key")

ROOT_DIR = Path(__file__).resolve().parent.parent


async def ensure_database_exists(url: str) -> None:
    parsed = make_url(url)
    admin_url = parsed.set(database="postgres")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": parsed.database},
            )
            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    finally:
        await engine.dispose()


def run_migrations() -> None:
    config = Config(str(ROOT_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT_DIR / "migrations"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
async def database() -> None:
    await ensure_database_exists(TEST_DATABASE_URL)
    await asyncio.to_thread(run_migrations)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from watchlist.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
