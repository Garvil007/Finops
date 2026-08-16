"""Liveness and readiness probes."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from finopsai.api.deps import get_session
from finopsai.api.schemas import HealthResponse
from finopsai.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Liveness",
    description="Answers as long as the process is running. Never touches the database.",
)
async def healthz() -> HealthResponse:
    """Report that the process is alive."""
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=HealthResponse,
    summary="Readiness",
    description="Reports 503 while the warehouse is unreachable, so traffic is held back.",
)
async def readyz(
    response: Response, session: Annotated[AsyncSession, Depends(get_session)]
) -> HealthResponse:
    """Check the dependencies needed to serve a request."""
    try:
        await session.execute(sa.text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 - any failure means not ready
        log.warning("readiness_check_failed", error=str(error))
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="unavailable", checks={"database": "error"})

    return HealthResponse(status="ok", checks={"database": "ok"})
