from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from watchlist.db import get_session
from watchlist.main import create_app


async def test_health_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_reports_database_failure() -> None:
    engine = create_async_engine(
        "postgresql+asyncpg://nobody:wrong@127.0.0.1:1/missing",
        connect_args={"timeout": 1},
    )

    async def broken_session() -> AsyncIterator[AsyncSession]:
        async with async_sessionmaker(engine)() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = broken_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
    finally:
        await engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "error"}
