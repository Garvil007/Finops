"""Tests for aggregation and the allocation pass, against the warehouse."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.attribution.engine import (
    CostFilters,
    Period,
    UnknownDimensionError,
    aggregate_costs,
    unattributed_report,
)
from finopsai.attribution.models import CostRecord, CostSource
from finopsai.attribution.rules import (
    AllocationRule,
    AllocationStrategy,
    apply_allocation_rules,
    usage_weights,
)
from finopsai.attribution.tags import UNATTRIBUTED

START = datetime(2026, 8, 10, tzinfo=UTC)
PERIOD = Period(start=START, end=START + timedelta(days=1))
CENTS = Decimal("0.01")
TARGETS = ("search", "support-bot", "research")


def _record(
    dedup_key: str,
    amount: str,
    team: str = "search",
    source: CostSource = CostSource.LLM,
    project: str = "discovery",
    model: str | None = "gpt-4o-mini",
    hours: int = 1,
) -> CostRecord:
    return CostRecord(
        source=source,
        dedup_key=dedup_key,
        occurred_at=START + timedelta(hours=hours),
        amount_usd=Decimal(amount),
        quantity=Decimal(100),
        unit="tokens",
        model=model,
        team=team,
        project=project,
        agent_id="agent",
        use_case="rag",
        raw={},
        allocated=False,
    )


async def _seed(sessions: async_sessionmaker[AsyncSession], *records: CostRecord) -> None:
    async with sessions() as session, session.begin():
        session.add_all(records)


async def test_aggregate_groups_by_team_with_source_subtotals(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("a", "1.00", team="search", source=CostSource.LLM),
        _record("b", "2.00", team="search", source=CostSource.COMPUTE),
        _record("c", "4.00", team="research", source=CostSource.LLM),
    )

    # Act
    async with sessions() as session:
        groups = await aggregate_costs(session, ["team"], PERIOD)

    # Assert: ordered by spend, with the per-source split intact
    assert [group.key["team"] for group in groups] == ["research", "search"]
    search = next(group for group in groups if group.key["team"] == "search")
    assert search.total_usd.quantize(CENTS) == Decimal("3.00")
    assert search.by_source["llm"].quantize(CENTS) == Decimal("1.00")
    assert search.by_source["compute"].quantize(CENTS) == Decimal("2.00")
    assert search.record_count == 2


async def test_aggregate_supports_multiple_dimensions(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("a", "1.00", team="search", project="discovery"),
        _record("b", "3.00", team="search", project="model-lab"),
    )

    # Act
    async with sessions() as session:
        groups = await aggregate_costs(session, ["team", "project"], PERIOD)

    # Assert
    assert {(g.key["team"], g.key["project"]) for g in groups} == {
        ("search", "discovery"),
        ("search", "model-lab"),
    }


async def test_filters_narrow_the_result(sessions: async_sessionmaker[AsyncSession]) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("a", "1.00", team="search"),
        _record("b", "5.00", team="research"),
    )

    # Act
    async with sessions() as session:
        groups = await aggregate_costs(session, ["team"], PERIOD, CostFilters(teams=("research",)))

    # Assert
    assert len(groups) == 1
    assert groups[0].key["team"] == "research"


async def test_records_outside_the_period_are_excluded(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(sessions, _record("inside", "1.00"), _record("outside", "9.00", hours=48))

    # Act
    async with sessions() as session:
        groups = await aggregate_costs(session, ["team"], PERIOD)

    # Assert: the window is half-open, so tomorrow is not today's spend
    assert groups[0].total_usd.quantize(CENTS) == Decimal("1.00")


async def test_unknown_dimensions_are_rejected(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        with pytest.raises(UnknownDimensionError, match="unknown dimension"):
            await aggregate_costs(session, ["team; drop table cost_record"], PERIOD)
        with pytest.raises(ValueError, match="at least one dimension"):
            await aggregate_costs(session, [], PERIOD)


def test_period_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="after period start"):
        Period(start=START, end=START)


async def test_unattributed_report_names_the_culprits(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("owned", "6.00", team="search"),
        _record("orphan-1", "3.00", team=UNATTRIBUTED, model="gpt-4o-mini"),
        _record("orphan-2", "1.00", team=UNATTRIBUTED, model="claude-haiku-4-5"),
    )

    # Act
    async with sessions() as session:
        report = await unattributed_report(session, PERIOD)

    # Assert
    assert report.total_usd.quantize(CENTS) == Decimal("10.00")
    assert report.unattributed_usd.quantize(CENTS) == Decimal("4.00")
    assert report.attributed_usd.quantize(CENTS) == Decimal("6.00")
    assert report.unattributed_share.quantize(CENTS) == Decimal("0.40")
    assert report.top_models[0].label == "gpt-4o-mini"
    assert report.top_models[0].amount_usd.quantize(CENTS) == Decimal("3.00")


async def test_unattributed_share_is_zero_when_there_is_no_spend(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Act
    async with sessions() as session:
        report = await unattributed_report(session, PERIOD)

    # Assert: no division by zero on an empty warehouse
    assert report.unattributed_share == Decimal(0)


async def test_usage_weights_reflect_existing_spend(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("a", "60.00", team="search"),
        _record("b", "30.00", team="support-bot"),
        _record("c", "10.00", team="research"),
    )

    # Act
    async with sessions() as session:
        weights = await usage_weights(session, PERIOD, TARGETS, (CostSource.LLM,))

    # Assert
    assert weights["search"].quantize(CENTS) == Decimal("60.00")
    assert weights["research"].quantize(CENTS) == Decimal("10.00")


async def test_usage_weights_fall_back_to_even_when_nobody_has_usage(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Act
    async with sessions() as session:
        weights = await usage_weights(session, PERIOD, TARGETS, (CostSource.LLM,))

    # Assert: with no signal, an even split beats excluding everyone
    assert set(weights.values()) == {Decimal(1)}


async def test_allocation_splits_shared_cost_and_keeps_the_parent(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange: shared vector DB cost, plus LLM usage to weight it by
    await _seed(
        sessions,
        _record("llm-search", "60.00", team="search"),
        _record("llm-support", "30.00", team="support-bot"),
        _record("llm-research", "10.00", team="research"),
        _record(
            "shared-vdb",
            "100.00",
            team=UNATTRIBUTED,
            source=CostSource.VECTORDB,
            project="shared",
            model=None,
        ),
    )
    rule = AllocationRule(
        name="shared-vector-db",
        strategy=AllocationStrategy.USAGE_WEIGHTED,
        source=CostSource.VECTORDB,
        targets=TARGETS,
    )

    # Act
    async with sessions() as session, session.begin():
        summary = await apply_allocation_rules(session, PERIOD, [rule])

    # Assert
    assert summary.parents_allocated == 1
    assert summary.children_created == 3

    async with sessions() as session:
        parent = await session.scalar(
            sa.select(CostRecord).where(CostRecord.dedup_key == "shared-vdb")
        )
        children = list(
            await session.scalars(
                sa.select(CostRecord).where(CostRecord.allocation_parent_id.is_not(None))
            )
        )

    assert parent is not None
    assert parent.allocated is True
    assert parent.amount_usd.quantize(CENTS) == Decimal("100.00")
    assert sum(child.amount_usd for child in children).quantize(CENTS) == Decimal("100.00")

    by_team = {child.team: child.amount_usd.quantize(CENTS) for child in children}
    assert by_team == {
        "search": Decimal("60.00"),
        "support-bot": Decimal("30.00"),
        "research": Decimal("10.00"),
    }


async def test_allocated_parents_are_excluded_from_totals(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("shared", "9.00", team=UNATTRIBUTED, source=CostSource.VECTORDB, model=None),
    )
    rule = AllocationRule(name="even", strategy=AllocationStrategy.EVEN_SPLIT, targets=TARGETS)

    # Act
    async with sessions() as session, session.begin():
        await apply_allocation_rules(session, PERIOD, [rule])

    async with sessions() as session:
        groups = await aggregate_costs(session, ["team"], PERIOD)
        with_parents = await aggregate_costs(
            session, ["team"], PERIOD, CostFilters(include_allocated_parents=True)
        )

    # Assert: the money is counted once, and the parent is still on record
    assert sum(group.total_usd for group in groups).quantize(CENTS) == Decimal("9.00")
    assert sum(g.total_usd for g in with_parents).quantize(CENTS) == Decimal("18.00")
    assert UNATTRIBUTED not in {group.key["team"] for group in groups}


async def test_allocation_is_idempotent(sessions: async_sessionmaker[AsyncSession]) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("shared", "9.00", team=UNATTRIBUTED, source=CostSource.VECTORDB, model=None),
    )
    rule = AllocationRule(name="even", strategy=AllocationStrategy.EVEN_SPLIT, targets=TARGETS)

    # Act
    async with sessions() as session, session.begin():
        first = await apply_allocation_rules(session, PERIOD, [rule])
    async with sessions() as session, session.begin():
        second = await apply_allocation_rules(session, PERIOD, [rule])

    # Assert
    assert first.children_created == 3
    assert second.children_created == 0

    async with sessions() as session:
        total = await session.scalar(sa.select(sa.func.count()).select_from(CostRecord))
    assert total == 4


async def test_records_without_a_rule_are_left_alone(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed(sessions, _record("owned", "5.00", team="search"))
    rule = AllocationRule(
        name="vectordb-only",
        strategy=AllocationStrategy.EVEN_SPLIT,
        targets=TARGETS,
        source=CostSource.VECTORDB,
    )

    # Act
    async with sessions() as session, session.begin():
        summary = await apply_allocation_rules(session, PERIOD, [rule])

    # Assert
    assert summary.parents_allocated == 0
    assert summary.skipped_no_rule == 1
