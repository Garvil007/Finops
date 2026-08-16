"""Cost query routes."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from finopsai.api.deps import get_session
from finopsai.api.schemas import (
    CostGroupModel,
    CostQueryResponse,
    PeriodModel,
    TimeseriesPointModel,
    TimeseriesResponse,
    UnattributedResponse,
    UnattributedSliceModel,
)
from finopsai.attribution.engine import (
    CostFilters,
    UnknownDimensionError,
    aggregate_costs,
    aggregate_timeseries,
    unattributed_report,
)
from finopsai.attribution.models import CostSource
from finopsai.attribution.periods import (
    Interval,
    InvalidPeriodError,
    change_percent,
    resolve_period,
)

router = APIRouter(prefix="/api/v1/costs", tags=["costs"])

DEFAULT_PERIOD = "30d"
INTERVAL_TO_TRUNC = {Interval.HOUR: "hour", Interval.DAY: "day", Interval.WEEK: "week"}

PeriodQuery = Annotated[str, Query(description="Relative window, e.g. 30d, 24h, 12w.")]
GroupByQuery = Annotated[
    str, Query(description="Comma separated dimensions: team, project, agent_id, use_case, model.")
]


def _split(value: str | None) -> tuple[str, ...]:
    """Parse a comma separated query parameter."""
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _period_model(period: object, label: str | None = None) -> PeriodModel:
    return PeriodModel(start=period.start, end=period.end, label=label)  # type: ignore[attr-defined]


def _filters(
    team: str | None, project: str | None, source: str | None, use_case: str | None
) -> CostFilters:
    """Build engine filters from query parameters."""
    try:
        sources = tuple(CostSource(value) for value in _split(source))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return CostFilters(
        teams=_split(team),
        projects=_split(project),
        use_cases=_split(use_case),
        sources=sources,
    )


@router.get(
    "",
    response_model=CostQueryResponse,
    summary="Grouped spend with period comparison",
    description=(
        "Totals per dimension key with a per-source subtotal, compared against "
        "the equally sized window immediately before. Spend that was split by an "
        "allocation rule is counted once, on the child records."
    ),
)
async def get_costs(
    session: Annotated[AsyncSession, Depends(get_session)],
    group_by: GroupByQuery = "team",
    period: PeriodQuery = DEFAULT_PERIOD,
    team: str | None = None,
    project: str | None = None,
    source: str | None = None,
    use_case: str | None = None,
) -> CostQueryResponse:
    """Return grouped totals for a window and its predecessor."""
    try:
        window = resolve_period(period)
        dimensions = list(_split(group_by))
        filters = _filters(team, project, source, use_case)
        current = await aggregate_costs(session, dimensions, window.current, filters)
        previous = await aggregate_costs(session, dimensions, window.previous, filters)
    except (InvalidPeriodError, UnknownDimensionError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    baseline = {tuple(sorted(group.key.items())): group.total_usd for group in previous}

    groups = []
    for group in current:
        key = tuple(sorted(group.key.items()))
        prior = baseline.get(key)
        groups.append(
            CostGroupModel(
                key=dict(group.key),
                total_usd=group.total_usd,
                by_source=dict(group.by_source),
                record_count=group.record_count,
                previous_total_usd=prior,
                change_percent=change_percent(group.total_usd, prior)
                if prior is not None
                else None,
            )
        )

    total = sum((group.total_usd for group in current), start=Decimal(0))
    previous_total = sum((group.total_usd for group in previous), start=Decimal(0))

    return CostQueryResponse(
        period=_period_model(window.current, window.label),
        comparison_period=_period_model(window.previous),
        total_usd=total,
        previous_total_usd=previous_total,
        change_percent=change_percent(total, previous_total),
        groups=groups,
    )


@router.get(
    "/unattributed",
    response_model=UnattributedResponse,
    summary="Spend with no owner",
    description=(
        "How much spend cannot be attributed to a team, and which models and "
        "resources are responsible. This is the report that turns a tagging gap "
        "into an action item."
    ),
)
async def get_unattributed(
    session: Annotated[AsyncSession, Depends(get_session)],
    period: PeriodQuery = DEFAULT_PERIOD,
) -> UnattributedResponse:
    """Return the unattributed spend report."""
    try:
        window = resolve_period(period)
    except InvalidPeriodError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    report = await unattributed_report(session, window.current)

    return UnattributedResponse(
        period=_period_model(window.current, window.label),
        total_usd=report.total_usd,
        unattributed_usd=report.unattributed_usd,
        attributed_usd=report.attributed_usd,
        unattributed_share=float(report.unattributed_share),
        by_source=dict(report.by_source),
        top_models=[
            UnattributedSliceModel(label=item.label, amount_usd=item.amount_usd)
            for item in report.top_models
        ],
        top_resources=[
            UnattributedSliceModel(label=item.label, amount_usd=item.amount_usd)
            for item in report.top_resources
        ],
    )


@router.get(
    "/timeseries",
    response_model=TimeseriesResponse,
    summary="Bucketed spend for dashboards",
    description="One point per dimension key per bucket, ordered oldest first.",
)
async def get_timeseries(
    session: Annotated[AsyncSession, Depends(get_session)],
    group_by: GroupByQuery = "team",
    period: PeriodQuery = DEFAULT_PERIOD,
    interval: Interval = Interval.DAY,
    team: str | None = None,
    project: str | None = None,
    source: str | None = None,
) -> TimeseriesResponse:
    """Return a bucketed series per dimension key."""
    try:
        window = resolve_period(period)
        points = await aggregate_timeseries(
            session,
            list(_split(group_by)),
            window.current,
            INTERVAL_TO_TRUNC[interval],
            _filters(team, project, source, None),
        )
    except (InvalidPeriodError, UnknownDimensionError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return TimeseriesResponse(
        period=_period_model(window.current, window.label),
        interval=str(interval),
        points=[
            TimeseriesPointModel(
                bucket=point.bucket, key=dict(point.key), amount_usd=point.amount_usd
            )
            for point in points
        ],
    )
