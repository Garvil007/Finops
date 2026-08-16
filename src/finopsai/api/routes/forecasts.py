"""Forecast route."""

from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from finopsai.api.deps import get_session
from finopsai.api.schemas import ForecastResponse
from finopsai.attribution.engine import CostFilters, Period, total_spend
from finopsai.attribution.forecast import build_forecast, month_bounds
from finopsai.attribution.models import Budget, BudgetPeriod

router = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])


@router.get(
    "",
    response_model=ForecastResponse,
    summary="Project this month's spend for a team",
    description=(
        "Straight-line projection of the current month's run rate to month end, "
        "with the date an active monthly budget would be breached if that rate "
        "holds. Early in a month the projection is marked low confidence because "
        "it is extrapolating from very little. A v2 would model seasonality with "
        "Holt-Winters or Prophet."
    ),
)
async def get_forecast(
    team: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> ForecastResponse:
    """Return the month-end projection for one team."""
    now = datetime.now(UTC)
    period_start, period_end = month_bounds(now)

    spend = await total_spend(
        session,
        Period(start=period_start, end=now if now > period_start else period_end),
        CostFilters(teams=(team,)),
    )

    budget = await session.scalar(
        sa.select(Budget).where(
            Budget.team == team,
            Budget.period == BudgetPeriod.MONTHLY,
            Budget.is_active.is_(True),
        )
    )

    forecast = build_forecast(
        team=team,
        spend_to_date=spend,
        as_of=now,
        period_start=period_start,
        period_end=period_end,
        budget_limit=budget.limit_usd if budget is not None else None,
    )

    return ForecastResponse(
        team=forecast.team,
        period_start=forecast.period_start,
        period_end=forecast.period_end,
        as_of=forecast.as_of,
        spend_to_date=forecast.spend_to_date,
        elapsed_days=forecast.elapsed_days,
        daily_run_rate=forecast.daily_run_rate,
        projected_total=forecast.projected_total,
        confidence=forecast.confidence,
        budget_limit=forecast.budget_limit,
        projected_utilization=forecast.projected_utilization,
        breach_date=forecast.breach_date,
        will_breach=forecast.will_breach,
    )
