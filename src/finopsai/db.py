"""Async SQLAlchemy engines and session factories.

Two databases are in play. The warehouse holds the ``finops`` schema and is the
only one FinOpsAI writes to. The LiteLLM database is read exclusively: its
schema belongs to the proxy's Prisma migrations, and nothing here may alter it.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from finopsai.config import Settings


def create_warehouse_engine(settings: Settings) -> AsyncEngine:
    """Engine for the FinOpsAI warehouse."""
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_litellm_engine(settings: Settings) -> AsyncEngine:
    """Read-only engine for the LiteLLM spend-log database."""
    return create_async_engine(settings.litellm_db_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory that leaves instances usable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
