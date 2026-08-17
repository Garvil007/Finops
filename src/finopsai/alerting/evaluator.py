"""Budget threshold evaluation.

Runs on a schedule: for every active budget, work out period-to-date spend for
that team, compare it against the budget's thresholds, and announce the highest
one newly crossed.

Two rules keep it useful rather than noisy:

* a threshold announces once per period, so 80% does not repeat every cycle;
* crossing a *higher* threshold announces again, because 100% is news even when
  80% was already reported.

The utilisation gauge is refreshed for every budget on every cycle whether or
not anything fires, so a dashboard shows the current position rather than only
the moments something was announced.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.alerting.alerts import BudgetAlert, CostDriver, Notifier
from finopsai.alerting.state import AlertStateStore, period_key
from finopsai.attribution.engine import CostFilters, Period, aggregate_costs, total_spend
from finopsai.attribution.forecast import build_forecast, month_bounds
from finopsai.attribution.models import Budget, BudgetPeriod
from finopsai.logging import get_logger
from finopsai.metrics import ALERTS_FIRED, BUDGET_UTILIZATION

log = get_logger(__name__)

MAX_DRIVERS = 3
DEFAULT_INTERVAL_MINUTES = 15


def crossed_threshold(utilization: float, thresholds: Sequence[int]) -> int | None:
    """Highest threshold the utilisation has reached.

    The comparison is inclusive: a budget sitting exactly on 80% has crossed
    80%. Treating the boundary as not-yet-crossed would delay the alert until
    the next cycle, which is the cycle where it is already too late.
    """
    reached = [threshold for threshold in sorted(thresholds) if utilization * 100 >= threshold]
    return reached[-1] if reached else None


async def top_drivers(
    session: AsyncSession, team: str, window: Period, limit: int = MAX_DRIVERS
) -> list[CostDriver]:
    """The largest contributors to a team's spend in the period.

    Grouped by model, which is the actionable label for LLM spend; records with
    no model (compute, vector DB) fall back to their project.
    """
    groups = await aggregate_costs(session, ["model"], window, CostFilters(teams=(team,)))
    drivers = [
        CostDriver(label=group.key["model"] or "untagged resource", amount_usd=group.total_usd)
        for group in groups
        if group.total_usd > Decimal(0)
    ]
    if drivers:
        return drivers[:limit]

    fallback = await aggregate_costs(session, ["project"], window, CostFilters(teams=(team,)))
    return [
        CostDriver(label=group.key["project"], amount_usd=group.total_usd)
        for group in fallback[:limit]
    ]


async def evaluate_budget(
    session: AsyncSession,
    budget: Budget,
    state: AlertStateStore,
    notifier: Notifier,
    now: datetime,
    dashboard_url: str | None = None,
) -> BudgetAlert | None:
    """Evaluate one budget, announcing a newly crossed threshold.

    Returns the alert that was raised, or None when nothing is new.
    """
    start, end = month_bounds(now)
    # Spend so far this period. Half-open, ending now rather than at period end,
    # so a partially elapsed month is not compared against a full month of spend.
    window = Period(start=start, end=now if now > start else end)

    spend = await total_spend(session, window, CostFilters(teams=(budget.team,)))
    limit = budget.limit_usd
    utilization = float(spend / limit) if limit > Decimal(0) else 0.0

    BUDGET_UTILIZATION.labels(team=budget.team).set(utilization)

    threshold = crossed_threshold(utilization, budget.alert_thresholds)
    if threshold is None:
        return None

    key = period_key(now, str(budget.period))
    already = await state.highest_fired(budget.id, key)
    if already is not None and threshold <= already:
        return None

    forecast = build_forecast(
        team=budget.team,
        spend_to_date=spend,
        as_of=now,
        period_start=start,
        period_end=end,
        budget_limit=limit,
    )

    alert = BudgetAlert(
        team=budget.team,
        budget_id=budget.id,
        threshold=threshold,
        period_key=key,
        period_start=start,
        period_end=end,
        spend_to_date=spend,
        budget_limit=limit,
        utilization=utilization,
        projected_total=forecast.projected_total,
        breach_date=forecast.breach_date,
        drivers=await top_drivers(session, budget.team, window),
        dashboard_url=dashboard_url,
    )

    await notifier.send(alert)
    await state.record_fired(budget.id, key, threshold)

    ALERTS_FIRED.labels(team=budget.team, threshold=str(threshold)).inc()
    log.info(
        "budget_alert_fired",
        team=budget.team,
        threshold=threshold,
        utilization=round(utilization, 4),
        spend_usd=str(spend),
        budget_usd=str(limit),
        breach_date=forecast.breach_date.isoformat() if forecast.breach_date else None,
    )
    return alert


async def evaluate_budgets(
    session: AsyncSession,
    state: AlertStateStore,
    notifier: Notifier,
    now: datetime | None = None,
    dashboard_url: str | None = None,
) -> list[BudgetAlert]:
    """Evaluate every active budget, returning the alerts that were raised."""
    moment = now or datetime.now(UTC)
    budgets = list(
        await session.scalars(
            sa.select(Budget).where(
                Budget.is_active.is_(True),
                # Weekly and daily budgets are stored but not yet evaluated: the
                # period-to-date window below is month based. Evaluating them
                # against month bounds would silently report the wrong number,
                # so they are skipped until the windows are generalised.
                Budget.period == BudgetPeriod.MONTHLY,
            )
        )
    )

    alerts: list[BudgetAlert] = []
    for budget in budgets:
        try:
            alert = await evaluate_budget(session, budget, state, notifier, moment, dashboard_url)
        except Exception:
            # One malformed budget must not stop the rest from being evaluated.
            log.exception("budget_evaluation_failed", budget_id=budget.id, team=budget.team)
            continue
        if alert is not None:
            alerts.append(alert)

    log.info("budget_evaluation_complete", budgets=len(budgets), alerts_fired=len(alerts))
    return alerts


class BudgetEvaluator:
    """Runs an evaluation cycle against its own session."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        state: AlertStateStore,
        notifier: Notifier,
        dashboard_url: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._state = state
        self._notifier = notifier
        self._dashboard_url = dashboard_url

    async def run_once(self) -> list[BudgetAlert]:
        """Evaluate every active budget once."""
        async with self._sessions() as session:
            return await evaluate_budgets(
                session, self._state, self._notifier, dashboard_url=self._dashboard_url
            )
