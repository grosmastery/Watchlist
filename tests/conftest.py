import asyncio
import os
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import respx
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

if TYPE_CHECKING:
    from watchlist.integrations.tmdb import TMDBClient

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://watchlist:watchlist@localhost:5433/watchlist_test",
)

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key")

ROOT_DIR = Path(__file__).resolve().parent.parent
CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')


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


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    await ensure_database_exists(TEST_DATABASE_URL)
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(TEST_DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
def tmdb_mock() -> Iterator[respx.MockRouter]:
    from watchlist.integrations.tmdb import BASE_URL

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture(scope="session")
async def tmdb_client() -> AsyncIterator["TMDBClient"]:
    from watchlist.integrations.tmdb import TMDBClient

    client = TMDBClient("test-key", backoff=0)
    yield client
    await client.aclose()


@pytest.fixture
def app(db_session: AsyncSession, tmdb_client: "TMDBClient") -> FastAPI:
    from watchlist.db import get_session
    from watchlist.integrations.dependencies import get_tmdb_client
    from watchlist.main import create_app

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_tmdb_client] = lambda: tmdb_client
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def register_user(
    client: AsyncClient,
    email: str = "user@example.com",
    username: str = "viewer",
    password: str = "secret-pass",
) -> None:
    token = await fetch_csrf_token(client, "/register")
    response = await client.post(
        "/register",
        data={
            "csrf_token": token,
            "email": email,
            "username": username,
            "password": password,
            "password_confirm": password,
        },
    )
    assert response.status_code == 303


async def fetch_csrf_token(client: AsyncClient, path: str = "/login") -> str:
    response = await client.get(path)
    match = CSRF_PATTERN.search(response.text)
    assert match is not None
    return match.group(1)
