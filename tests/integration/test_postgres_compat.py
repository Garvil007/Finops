"""Postgres-only behaviour that SQLite cannot prove.

Everything here is skipped unless FINOPSAI_TEST_POSTGRES_URL is set, which CI
does. These are the paths where the two dialects genuinely differ: the finops
schema, date_trunc bucketing, and exact numeric arithmetic on money.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.attribution.engine import (
    CostFilters,
    Period,
    aggregate_costs,
    aggregate_timeseries,
    unattributed_report,
)
from finopsai.attribution.models import CostRecord, CostSource
from finopsai.attribution.rules import AllocationRule, AllocationStrategy, apply_allocation_rules
from finopsai.attribution.tags import UNATTRIBUTED
from tests.conftest import requires_postgres

NOW = datetime.now(UTC)
PERIOD = Period(start=NOW - timedelta(days=7), end=NOW + timedelta(minutes=1))


def _record(
    key: str,
    amount: str,
    team: str = "search",
    source: CostSource = CostSource.LLM,
    hours_ago: int = 1,
    model: str | None = "gpt-4o-mini",
) -> CostRecord:
    return CostRecord(
        source=source,
        dedup_key=key,
        occurred_at=NOW - timedelta(hours=hours_ago),
        amount_usd=Decimal(amount),
        quantity=Decimal("1000"),
        unit="tokens",
        model=model,
        team=team,
        project="discovery",
        agent_id="agent",
        use_case="rag",
        raw={"simulated": False},
        allocated=False,
    )


async def _seed(sessions: async_sessionmaker[AsyncSession], *records: CostRecord) -> None:
    async with sessions() as session, session.begin():
        session.add_all(records)


@requires_postgres
async def test_schema_and_enum_round_trip(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(postgres_sessions, _record("a", "1.00", source=CostSource.VECTORDB))

    # Act
    async with postgres_sessions() as session:
        stored = await session.scalar(sa.select(CostRecord).where(CostRecord.dedup_key == "a"))

    # Assert
    assert stored is not None
    assert stored.source is CostSource.VECTORDB


@requires_postgres
async def test_money_keeps_full_precision(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange: eight decimal places, which a float would round away
    await _seed(postgres_sessions, _record("precise", "0.00000001"))

    # Act
    async with postgres_sessions() as session:
        stored = await session.scalar(
            sa.select(CostRecord).where(CostRecord.dedup_key == "precise")
        )

    # Assert
    assert stored is not None
    assert stored.amount_usd == Decimal("0.00000001")
    assert isinstance(stored.amount_usd, Decimal)


@requires_postgres
async def test_aggregation_sums_exactly(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange: three amounts a float would not sum cleanly
    await _seed(
        postgres_sessions,
        _record("a", "0.10"),
        _record("b", "0.20"),
        _record("c", "0.30"),
    )

    # Act
    async with postgres_sessions() as session:
        groups = await aggregate_costs(session, ["team"], PERIOD)

    # Assert
    assert groups[0].total_usd == Decimal("0.60000000")


@requires_postgres
async def test_date_trunc_bucketing(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange: two records in one hour, one in another
    await _seed(
        postgres_sessions,
        _record("h1a", "1.00", hours_ago=3),
        _record("h1b", "2.00", hours_ago=3),
        _record("h2", "4.00", hours_ago=2),
    )

    # Act: this is the date_trunc path, which SQLite never executes
    async with postgres_sessions() as session:
        points = await aggregate_timeseries(session, ["team"], PERIOD, "hour")

    # Assert
    assert len(points) == 2
    assert points[0].bucket < points[1].bucket
    assert {point.amount_usd for point in points} == {
        Decimal("3.00000000"),
        Decimal("4.00000000"),
    }


@requires_postgres
async def test_day_and_week_bucketing_are_supported(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(postgres_sessions, _record("d", "5.00", hours_ago=2))

    # Act / Assert: week truncation silently degrades to day on SQLite, so this
    # is the only place it is genuinely exercised.
    async with postgres_sessions() as session:
        for trunc in ("day", "week"):
            points = await aggregate_timeseries(session, ["team"], PERIOD, trunc)
            assert len(points) == 1


@requires_postgres
async def test_unattributed_report(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(
        postgres_sessions,
        _record("owned", "6.00", team="search"),
        _record("orphan", "4.00", team=UNATTRIBUTED),
    )

    # Act
    async with postgres_sessions() as session:
        report = await unattributed_report(session, PERIOD)

    # Assert
    assert report.total_usd == Decimal("10.00000000")
    assert report.unattributed_usd == Decimal("4.00000000")
    assert round(float(report.unattributed_share), 2) == 0.4


@requires_postgres
async def test_allocation_children_sum_to_the_parent(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange: ten dollars across three teams is the penny problem
    await _seed(
        postgres_sessions,
        _record("shared", "10.00", team=UNATTRIBUTED, source=CostSource.VECTORDB, model=None),
    )
    rule = AllocationRule(
        name="even",
        strategy=AllocationStrategy.EVEN_SPLIT,
        targets=("search", "support-bot", "research"),
    )

    # Act
    async with postgres_sessions() as session, session.begin():
        await apply_allocation_rules(session, PERIOD, [rule])

    # Assert
    async with postgres_sessions() as session:
        children = list(
            await session.scalars(
                sa.select(CostRecord).where(CostRecord.allocation_parent_id.is_not(None))
            )
        )
        groups = await aggregate_costs(session, ["team"], PERIOD)

    assert len(children) == 3
    assert sum(child.amount_usd for child in children) == Decimal("10.00000000")
    # The parent is excluded, so the money is counted exactly once.
    assert sum(group.total_usd for group in groups) == Decimal("10.00000000")


@requires_postgres
async def test_filters_and_json_column_survive_the_round_trip(
    postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(
        postgres_sessions,
        _record("a", "1.00", team="search"),
        _record("b", "9.00", team="research"),
    )

    # Act
    async with postgres_sessions() as session:
        groups = await aggregate_costs(
            session, ["team", "model"], PERIOD, CostFilters(teams=("research",))
        )
        stored = await session.scalar(sa.select(CostRecord).where(CostRecord.dedup_key == "b"))

    # Assert
    assert len(groups) == 1
    assert groups[0].key == {"team": "research", "model": "gpt-4o-mini"}
    assert stored is not None
    assert stored.raw == {"simulated": False}
