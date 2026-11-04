from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.db import get_session

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "error"]


@router.get("/health")
async def health(
    response: Response, session: Annotated[AsyncSession, Depends(get_session)]
) -> HealthStatus:
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatus(status="error", database="error")
    return HealthStatus(status="ok", database="ok")
