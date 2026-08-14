"""Shared pytest fixtures.

Integration tests run against in-memory SQLite so the suite stays hermetic and
CI needs no Postgres. The models live in the ``finops`` schema, which SQLite has
no concept of, so every engine carries a schema translation to the default
schema. Production keeps the real schema.
"""

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine
from sqlalchemy.pool import StaticPool

from finopsai.attribution.models import SCHEMA, Base
from finopsai.collectors.litellm_spend import SPEND_LOGS
from finopsai.config import Settings

IN_MEMORY_URL = "sqlite+aiosqlite://"


def _memory_engine() -> AsyncEngine:
    """An in-memory engine whose single connection is shared by all sessions."""
    return _create_async_engine(
        IN_MEMORY_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    ).execution_options(schema_translate_map={SCHEMA: None})


@pytest.fixture
def settings() -> Settings:
    """Return settings built from defaults, ignoring any developer ``.env``."""
    return Settings(_env_file=None)


@pytest.fixture
async def warehouse_engine() -> AsyncIterator[AsyncEngine]:
    """A warehouse engine with the finops tables created."""
    engine = _memory_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(warehouse_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the warehouse engine."""
    return async_sessionmaker(warehouse_engine, expire_on_commit=False)


@pytest.fixture
async def litellm_engine() -> AsyncIterator[AsyncEngine]:
    """A stand-in for the proxy's database, with an empty spend-log table."""
    engine = _memory_engine()
    async with engine.begin() as connection:
        await connection.run_sync(SPEND_LOGS.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def insert_spend_log(litellm_engine: AsyncEngine):  # noqa: ANN201 - local helper factory
    """Return a coroutine that inserts one spend-log row."""

    async def _insert(**values: object) -> None:
        async with litellm_engine.begin() as connection:
            await connection.execute(sa.insert(SPEND_LOGS).values(**values))

    return _insert
