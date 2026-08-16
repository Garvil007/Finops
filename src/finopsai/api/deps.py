"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.config import Settings, get_settings
from finopsai.db import create_session_factory, create_warehouse_engine

_sessions: async_sessionmaker[AsyncSession] | None = None


def session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """Process-wide warehouse session factory, built once."""
    global _sessions  # noqa: PLW0603 - one engine per process is the point
    if _sessions is None:
        _sessions = create_session_factory(create_warehouse_engine(settings or get_settings()))
    return _sessions


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session.

    Overridden wholesale in tests, which is why the factory lookup happens here
    rather than at import time.
    """
    async with session_factory()() as session:
        yield session
