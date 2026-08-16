"""Integration tests for the simulated compute and vector DB collectors."""

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.attribution.models import CostRecord, CostSource
from finopsai.collectors.compute import MockComputeCollector
from finopsai.collectors.vectordb import MockVectorDBCollector

ONE_DAY = 1
HOURS_PER_DAY = 24


async def _records(sessions: async_sessionmaker[AsyncSession]) -> list[CostRecord]:
    async with sessions() as session:
        result = await session.scalars(sa.select(CostRecord).order_by(CostRecord.occurred_at))
        return list(result.all())


async def test_compute_backfills_one_record_per_team_hour(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    collector = MockComputeCollector(sessions, backfill_days=ONE_DAY)

    # Act
    written = await collector.run_once()

    # Assert: three teams for each completed hour in the window
    records = await _records(sessions)
    assert written == len(records)
    assert written in {HOURS_PER_DAY * 3, (HOURS_PER_DAY + 1) * 3}
    assert {record.source for record in records} == {CostSource.COMPUTE}


async def test_compute_rerun_writes_no_duplicates(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    collector = MockComputeCollector(sessions, backfill_days=ONE_DAY)
    first = await collector.run_once()

    # Act
    second = await collector.run_once()

    # Assert
    assert first > 0
    assert second == 0
    assert len(await _records(sessions)) == first


async def test_simulated_records_are_marked_and_keyed_as_mock(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Act
    await MockComputeCollector(sessions, backfill_days=ONE_DAY).run_once()

    # Assert: simulated spend must never look like measured spend
    for record in await _records(sessions):
        assert record.raw["simulated"] is True
        assert record.dedup_key.startswith("mock-compute:")


async def test_research_carries_the_gpu_spend(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Act
    await MockComputeCollector(sessions, backfill_days=ONE_DAY).run_once()
    records = await _records(sessions)

    # Assert
    by_team: dict[str, Decimal] = {}
    for record in records:
        by_team[record.team] = by_team.get(record.team, Decimal(0)) + record.amount_usd

    assert by_team["research"] > by_team["search"]
    assert by_team["research"] > by_team["support-bot"]
    gpu_units = {r.unit for r in records if r.team == "research"}
    assert gpu_units == {"gpu-hour"}


async def test_amounts_are_reproducible_across_collector_instances(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    first = await MockComputeCollector(sessions, backfill_days=ONE_DAY).collect()
    second = await MockComputeCollector(sessions, backfill_days=ONE_DAY).collect()

    # Assert: a replay reproduces the same numbers, not merely the same keys
    by_key = {record.dedup_key: record.amount_usd for record in first}
    for record in second:
        if record.dedup_key in by_key:
            assert record.amount_usd == by_key[record.dedup_key]


async def test_vectordb_is_keyed_per_index_and_maps_to_projects(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Act
    written = await MockVectorDBCollector(sessions, backfill_days=ONE_DAY).run_once()
    records = await _records(sessions)

    # Assert
    assert written == len(records)
    assert {record.source for record in records} == {CostSource.VECTORDB}
    assert {record.project for record in records} == {
        "customer-support",
        "discovery",
        "model-lab",
    }
    assert all(record.dedup_key.startswith("mock-vectordb:") for record in records)
    assert all(record.unit == "read-unit" for record in records)


async def test_vectordb_rerun_writes_no_duplicates(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    collector = MockVectorDBCollector(sessions, backfill_days=ONE_DAY)
    first = await collector.run_once()

    # Act
    second = await collector.run_once()

    # Assert
    assert first > 0
    assert second == 0


async def test_sources_coexist_without_dedup_collisions(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Act
    compute = await MockComputeCollector(sessions, backfill_days=ONE_DAY).run_once()
    vectordb = await MockVectorDBCollector(sessions, backfill_days=ONE_DAY).run_once()

    # Assert
    records = await _records(sessions)
    assert len(records) == compute + vectordb
    assert {record.source for record in records} == {CostSource.COMPUTE, CostSource.VECTORDB}
