"""Aggregation over the cost warehouse.

Two questions this module answers, and they are the ones that make the product
FinOps rather than logging:

* what did each owner spend, broken down by where the money went;
* how much spend has no owner at all, and which models or resources are
  responsible for it.

Aggregation happens in SQL. The database returns one row per group, not one row
per cost record, so response size and work scale with the number of groups
rather than the volume of spend.

Records that have been split by an allocation rule are excluded by default.
A parent keeps its full amount for audit, but its children carry the same money
attributed to the teams that caused it, so counting both would double the total.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from finopsai.attribution.models import CostRecord, CostSource
from finopsai.attribution.tags import UNATTRIBUTED

ZERO = Decimal(0)
DEFAULT_TOP_N = 10

# Only these names may reach a GROUP BY. Anything else is rejected rather than
# interpolated, so a caller-supplied dimension can never become SQL.
GROUPABLE: Mapping[str, InstrumentedAttribute[Any]] = {
    "team": CostRecord.team,
    "project": CostRecord.project,
    "agent_id": CostRecord.agent_id,
    "use_case": CostRecord.use_case,
    "model": CostRecord.model,
}


class UnknownDimensionError(ValueError):
    """Raised when a caller asks to group by something that is not a dimension."""

    def __init__(self, name: str) -> None:
        known = ", ".join(sorted(GROUPABLE))
        super().__init__(f"unknown dimension {name!r}; group by one of: {known}")


@dataclass(frozen=True)
class Period:
    """A half-open time window: start inclusive, end exclusive."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("period end must be after period start")


@dataclass(frozen=True)
class CostFilters:
    """Optional narrowing applied before aggregation."""

    teams: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    agent_ids: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    sources: tuple[CostSource, ...] = ()
    include_allocated_parents: bool = False


@dataclass(frozen=True)
class CostGroup:
    """One row of the answer: a dimension key with its totals."""

    key: Mapping[str, str]
    total_usd: Decimal
    by_source: Mapping[str, Decimal]
    record_count: int


@dataclass(frozen=True)
class UnattributedSlice:
    """One contributor to unattributed spend."""

    label: str
    amount_usd: Decimal


@dataclass(frozen=True)
class UnattributedReport:
    """The go-fix-your-tagging report."""

    period: Period
    total_usd: Decimal
    unattributed_usd: Decimal
    top_models: Sequence[UnattributedSlice] = field(default_factory=tuple)
    top_resources: Sequence[UnattributedSlice] = field(default_factory=tuple)
    by_source: Mapping[str, Decimal] = field(default_factory=dict)

    @property
    def attributed_usd(self) -> Decimal:
        """Spend that has an owner."""
        return self.total_usd - self.unattributed_usd

    @property
    def unattributed_share(self) -> Decimal:
        """Fraction of spend with no owner, between 0 and 1."""
        if self.total_usd == ZERO:
            return ZERO
        return self.unattributed_usd / self.total_usd


def _resolve_columns(group_by: Sequence[str]) -> list[InstrumentedAttribute[Any]]:
    """Map dimension names onto columns, rejecting anything unknown."""
    if not group_by:
        raise ValueError("group_by must name at least one dimension")

    columns: list[InstrumentedAttribute[Any]] = []
    for name in group_by:
        column = GROUPABLE.get(name)
        if column is None:
            raise UnknownDimensionError(name)
        columns.append(column)
    return columns


def _conditions(period: Period, filters: CostFilters) -> list[sa.ColumnElement[bool]]:
    """Build the WHERE clause shared by every query in this module."""
    conditions: list[sa.ColumnElement[bool]] = [
        CostRecord.occurred_at >= period.start,
        CostRecord.occurred_at < period.end,
    ]

    if not filters.include_allocated_parents:
        # A split parent is superseded by its children; counting both doubles it.
        conditions.append(CostRecord.allocated.is_(False))

    for column, values in (
        (CostRecord.team, filters.teams),
        (CostRecord.project, filters.projects),
        (CostRecord.agent_id, filters.agent_ids),
        (CostRecord.use_case, filters.use_cases),
    ):
        if values:
            conditions.append(column.in_(values))

    if filters.sources:
        conditions.append(CostRecord.source.in_(filters.sources))

    return conditions


def _amount() -> sa.ColumnElement[Decimal]:
    """Summed spend, coerced back to an exact decimal."""
    return sa.cast(sa.func.coalesce(sa.func.sum(CostRecord.amount_usd), 0), sa.Numeric(18, 8))


async def aggregate_costs(
    session: AsyncSession,
    group_by: Sequence[str],
    period: Period,
    filters: CostFilters | None = None,
) -> list[CostGroup]:
    """Total spend per dimension key, with a subtotal per cost source.

    The database groups by the requested dimensions *and* by source in a single
    pass. Folding those rows into per-key subtotals happens here, over one row
    per group rather than one row per cost record -- the aggregation itself is
    never done in Python.
    """
    active = filters or CostFilters()
    columns = _resolve_columns(group_by)

    query = (
        sa.select(
            *columns,
            CostRecord.source,
            _amount().label("amount_usd"),
            sa.func.count().label("record_count"),
        )
        .where(*_conditions(period, active))
        .group_by(*columns, CostRecord.source)
    )

    rows = (await session.execute(query)).all()

    totals: dict[tuple[str, ...], Decimal] = {}
    per_source: dict[tuple[str, ...], dict[str, Decimal]] = {}
    counts: dict[tuple[str, ...], int] = {}
    keys: dict[tuple[str, ...], Mapping[str, str]] = {}

    for row in rows:
        values = tuple(str(value) for value in row[: len(columns)])
        source = str(row[len(columns)])
        amount = Decimal(row.amount_usd or 0)

        totals[values] = totals.get(values, ZERO) + amount
        per_source.setdefault(values, {})[source] = amount
        counts[values] = counts.get(values, 0) + int(row.record_count)
        keys[values] = dict(zip(group_by, values, strict=True))

    groups = [
        CostGroup(
            key=keys[values],
            total_usd=total,
            by_source=per_source[values],
            record_count=counts[values],
        )
        for values, total in totals.items()
    ]
    groups.sort(key=lambda group: group.total_usd, reverse=True)
    return groups


async def _top_slices(
    session: AsyncSession,
    column: InstrumentedAttribute[Any],
    period: Period,
    limit: int,
) -> list[UnattributedSlice]:
    """Largest contributors to unattributed spend, by one column."""
    amount = _amount().label("amount_usd")
    query = (
        sa.select(column, amount)
        .where(
            *_conditions(period, CostFilters(teams=(UNATTRIBUTED,))),
        )
        .group_by(column)
        .order_by(sa.desc(amount))
        .limit(limit)
    )

    rows = (await session.execute(query)).all()
    return [
        UnattributedSlice(
            label=str(row[0]) if row[0] is not None else "unknown",
            amount_usd=Decimal(row.amount_usd or 0),
        )
        for row in rows
    ]


async def unattributed_report(
    session: AsyncSession, period: Period, top_n: int = DEFAULT_TOP_N
) -> UnattributedReport:
    """Quantify spend that has no owner, and name what is causing it.

    This is the report that turns a tagging gap into an action item: the share
    of spend nobody owns, plus the models and resources driving it.
    """
    totals_query = sa.select(_amount().label("amount_usd")).where(
        *_conditions(period, CostFilters())
    )
    total = Decimal((await session.execute(totals_query)).scalar_one() or 0)

    unattributed_query = sa.select(_amount().label("amount_usd")).where(
        *_conditions(period, CostFilters(teams=(UNATTRIBUTED,)))
    )
    unattributed = Decimal((await session.execute(unattributed_query)).scalar_one() or 0)

    by_source_query = (
        sa.select(CostRecord.source, _amount().label("amount_usd"))
        .where(*_conditions(period, CostFilters(teams=(UNATTRIBUTED,))))
        .group_by(CostRecord.source)
    )
    by_source = {
        str(row[0]): Decimal(row.amount_usd or 0)
        for row in (await session.execute(by_source_query)).all()
    }

    return UnattributedReport(
        period=period,
        total_usd=total,
        unattributed_usd=unattributed,
        top_models=await _top_slices(session, CostRecord.model, period, top_n),
        top_resources=await _top_slices(session, CostRecord.project, period, top_n),
        by_source=by_source,
    )


# Timestamp truncation has no portable spelling. Postgres has date_trunc; SQLite
# has strftime. The dialect is read from the bound engine rather than assumed.
SQLITE_TRUNC_FORMATS = {
    "hour": "%Y-%m-%dT%H:00:00",
    "day": "%Y-%m-%dT00:00:00",
    "week": "%Y-%m-%dT00:00:00",
}


@dataclass(frozen=True)
class TimeseriesPoint:
    """Spend for one dimension key in one time bucket."""

    bucket: datetime
    key: Mapping[str, str]
    amount_usd: Decimal


def _dialect_name(session: AsyncSession) -> str:
    """Name of the dialect backing this session."""
    try:
        return str(session.get_bind().dialect.name)
    except Exception:  # noqa: BLE001 - dialect detection must never break a query
        return "postgresql"


def _bucket_expression(dialect: str, trunc: str) -> sa.ColumnElement[Any]:
    """Truncate occurred_at to the requested bucket width."""
    if trunc not in SQLITE_TRUNC_FORMATS:
        raise ValueError(f"unknown interval {trunc!r}; expected hour, day or week")

    if dialect == "sqlite":
        if trunc == "week":
            # SQLite has no week truncation; %W would need locale-stable
            # week numbering, so weeks fall back to day buckets there.
            return sa.func.strftime(SQLITE_TRUNC_FORMATS["day"], CostRecord.occurred_at)
        return sa.func.strftime(SQLITE_TRUNC_FORMATS[trunc], CostRecord.occurred_at)

    return sa.func.date_trunc(trunc, CostRecord.occurred_at)


def _as_datetime(value: object) -> datetime:
    """Normalize a bucket value, which is a string on SQLite."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def aggregate_timeseries(
    session: AsyncSession,
    group_by: Sequence[str],
    period: Period,
    trunc: str = "day",
    filters: CostFilters | None = None,
) -> list[TimeseriesPoint]:
    """Spend per dimension key per time bucket, ordered oldest first.

    Shaped for a dashboard: the database does the bucketing and the summing, and
    returns one row per key per bucket.
    """
    active = filters or CostFilters()
    columns = _resolve_columns(group_by)
    bucket = _bucket_expression(_dialect_name(session), trunc).label("bucket")

    query = (
        sa.select(bucket, *columns, _amount().label("amount_usd"))
        .where(*_conditions(period, active))
        .group_by(bucket, *columns)
        .order_by(bucket)
    )

    rows = (await session.execute(query)).all()
    return [
        TimeseriesPoint(
            bucket=_as_datetime(row.bucket),
            key=dict(
                zip(
                    group_by,
                    (str(value) for value in row[1 : 1 + len(columns)]),
                    strict=True,
                )
            ),
            amount_usd=Decimal(row.amount_usd or 0),
        )
        for row in rows
    ]


async def total_spend(
    session: AsyncSession, period: Period, filters: CostFilters | None = None
) -> Decimal:
    """Total spend in a window, after allocation."""
    query = sa.select(_amount()).where(*_conditions(period, filters or CostFilters()))
    return Decimal((await session.execute(query)).scalar_one() or 0)
