"""Tests for engine construction."""

from finopsai.config import Settings
from finopsai.db import (
    create_litellm_engine,
    create_session_factory,
    create_warehouse_engine,
)


async def test_engines_target_their_own_databases(settings: Settings) -> None:
    # Act
    warehouse = create_warehouse_engine(settings)
    litellm = create_litellm_engine(settings)

    # Assert
    try:
        assert warehouse.url.database == "finopsai"
        assert litellm.url.database == "litellm"
        assert warehouse.dialect.is_async
        assert create_session_factory(warehouse).kw["bind"] is warehouse
    finally:
        await warehouse.dispose()
        await litellm.dispose()
