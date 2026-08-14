"""Integration tests for the LiteLLM spend collector.

Exercises the full path: a row in the proxy's spend-log table becomes an
attributed CostRecord in the warehouse, and running the collector again is a
no-op rather than a double-count.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from finopsai.attribution.models import CollectorWatermark, CostRecord, CostSource
from finopsai.attribution.tags import UNATTRIBUTED
from finopsai.collectors.litellm_spend import LiteLLMSpendCollector

InsertSpendLog = Callable[..., Awaitable[None]]

TAGGED_REQUEST = "req-tagged-001"
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


async def _records(sessions: async_sessionmaker[AsyncSession]) -> list[CostRecord]:
    async with sessions() as session:
        result = await session.scalars(sa.select(CostRecord).order_by(CostRecord.occurred_at))
        return list(result.all())


async def test_tagged_spend_log_becomes_an_attributed_cost_record(
    sessions: async_sessionmaker[AsyncSession],
    litellm_engine: AsyncEngine,
    insert_spend_log: InsertSpendLog,
) -> None:
    # Arrange
    await insert_spend_log(
        request_id=TAGGED_REQUEST,
        startTime=NOW,
        model="gpt-4o-mini",
        spend=0.00123456,
        total_tokens=1500,
        request_tags=["team:search", "agent_id:demo", "use_case:rag", "project:demo"],
        metadata={"user_api_key_team_alias": "ignored-because-tags-win"},
    )
    collector = LiteLLMSpendCollector(sessions, litellm_engine)

    # Act
    written = await collector.run_once()

    # Assert
    assert written == 1
    (record,) = await _records(sessions)
    assert record.source is CostSource.LLM
    assert record.dedup_key == TAGGED_REQUEST
    assert record.amount_usd == Decimal("0.00123456")
    assert record.quantity == Decimal(1500)
    assert record.unit == "tokens"
    assert record.model == "gpt-4o-mini"
    assert (record.team, record.project, record.agent_id, record.use_case) == (
        "search",
        "demo",
        "demo",
        "rag",
    )


async def test_second_run_writes_no_duplicates(
    sessions: async_sessionmaker[AsyncSession],
    litellm_engine: AsyncEngine,
    insert_spend_log: InsertSpendLog,
) -> None:
    # Arrange
    await insert_spend_log(
        request_id=TAGGED_REQUEST,
        startTime=NOW,
        model="gpt-4o-mini",
        spend=0.5,
        total_tokens=10,
        request_tags=["team:search"],
        metadata={},
    )
    collector = LiteLLMSpendCollector(sessions, litellm_engine)
    assert await collector.run_once() == 1

    # Act: the watermark window is inclusive, so this re-reads the same row
    written = await collector.run_once()

    # Assert
    assert written == 0
    assert len(await _records(sessions)) == 1


async def test_untagged_spend_is_kept_as_unattributed(
    sessions: async_sessionmaker[AsyncSession],
    litellm_engine: AsyncEngine,
    insert_spend_log: InsertSpendLog,
) -> None:
    # Arrange
    await insert_spend_log(
        request_id="req-untagged-001",
        startTime=NOW,
        model="claude-haiku-4-5",
        spend=0.25,
        total_tokens=40,
        request_tags=[],
        metadata={},
    )
    collector = LiteLLMSpendCollector(sessions, litellm_engine)

    # Act
    written = await collector.run_once()

    # Assert: unattributed spend is the metric, not an error
    assert written == 1
    (record,) = await _records(sessions)
    assert record.team == UNATTRIBUTED
    assert record.amount_usd == Decimal("0.25")


async def test_watermark_advances_and_later_rows_are_picked_up(
    sessions: async_sessionmaker[AsyncSession],
    litellm_engine: AsyncEngine,
    insert_spend_log: InsertSpendLog,
) -> None:
    # Arrange
    await insert_spend_log(
        request_id="req-first",
        startTime=NOW,
        model="gpt-4o-mini",
        spend=0.1,
        total_tokens=10,
        request_tags=["team:search"],
        metadata={},
    )
    collector = LiteLLMSpendCollector(sessions, litellm_engine)
    assert await collector.run_once() == 1

    async with sessions() as session:
        stored = await session.get(CollectorWatermark, collector.name)
    assert stored is not None
    assert stored.last_cursor == "req-first"

    # Act: a newer row arrives after the watermark
    await insert_spend_log(
        request_id="req-second",
        startTime=NOW + timedelta(minutes=5),
        model="gpt-4o-mini",
        spend=0.2,
        total_tokens=20,
        request_tags=["team:payments"],
        metadata={},
    )
    written = await collector.run_once()

    # Assert
    assert written == 1
    records = await _records(sessions)
    assert [r.dedup_key for r in records] == ["req-first", "req-second"]
    assert [r.team for r in records] == ["search", "payments"]


async def test_failed_cycle_is_isolated_and_does_not_raise(
    sessions: async_sessionmaker[AsyncSession],
    litellm_engine: AsyncEngine,
) -> None:
    # Arrange: drop the source table out from under the collector
    async with litellm_engine.begin() as connection:
        await connection.execute(sa.text('DROP TABLE "LiteLLM_SpendLogs"'))
    collector = LiteLLMSpendCollector(sessions, litellm_engine)

    # Act
    written = await collector.run_once()

    # Assert: the loop survives a broken cycle
    assert written == 0
    assert await _records(sessions) == []
