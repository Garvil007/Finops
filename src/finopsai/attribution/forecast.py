"""Spend forecasting.

MVP: a straight-line projection of the current month's run rate to month end,
plus the date a budget would be breached if that rate holds.

This is deliberately the simplest model that is still honest. It assumes spend
is uniform, which it is not -- weekday and weekend usage differ, and a run rate
measured over two days of a month says very little. The forecast therefore
carries a confidence signal rather than pretending precision it does not have.

A v2 would replace the straight line with a model that understands seasonality:
exponential smoothing (Holt-Winters) for weekly cycles, or Prophet where the
history is long enough to fit changepoints and holiday effects. Both need
several weeks of history, which is exactly what this warehouse is accumulating.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal(0)
CENT = Decimal("0.01")
HOURS_PER_DAY = Decimal(24)
# A run rate measured over less than an hour extrapolates to nonsense.
MIN_ELAPSED = timedelta(hours=1)
LOW_CONFIDENCE_DAYS = 3.0
MEDIUM_CONFIDENCE_DAYS = 10.0


class ForecastConfidence(StrEnum):
    """How much of the period the projection is based on."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Forecast:
    """A straight-line projection of one team's month."""

    team: str
    period_start: datetime
    period_end: datetime
    as_of: datetime
    spend_to_date: Decimal
    elapsed_days: float
    daily_run_rate: Decimal
    projected_total: Decimal
    confidence: ForecastConfidence
    budget_limit: Decimal | None = None
    projected_utilization: float | None = None
    breach_date: datetime | None = None

    @property
    def will_breach(self) -> bool:
        """True when the budget is projected to be exceeded."""
        return self.breach_date is not None


def month_bounds(moment: datetime) -> tuple[datetime, datetime]:
    """First instant of the month containing ``moment``, and of the next one."""
    start = moment.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _confidence(elapsed_days: float) -> ForecastConfidence:
    """Rate the projection by how much of the period it has seen."""
    if elapsed_days < LOW_CONFIDENCE_DAYS:
        return ForecastConfidence.LOW
    if elapsed_days < MEDIUM_CONFIDENCE_DAYS:
        return ForecastConfidence.MEDIUM
    return ForecastConfidence.HIGH


def _breach_date(
    as_of: datetime,
    period_end: datetime,
    spend_to_date: Decimal,
    daily_run_rate: Decimal,
    budget_limit: Decimal | None,
) -> datetime | None:
    """When the budget runs out, if it does before the period ends."""
    if budget_limit is None or budget_limit <= ZERO:
        return None
    if spend_to_date >= budget_limit:
        # Already over: the breach happened, report it as of now.
        return as_of
    if daily_run_rate <= ZERO:
        return None

    remaining = budget_limit - spend_to_date
    days_left = remaining / daily_run_rate
    breach = as_of + timedelta(days=float(days_left))
    return breach if breach < period_end else None


def build_forecast(
    team: str,
    spend_to_date: Decimal,
    as_of: datetime,
    period_start: datetime,
    period_end: datetime,
    budget_limit: Decimal | None = None,
) -> Forecast:
    """Project spend to the end of the period at the observed run rate.

    On the first day of a month the elapsed window is a few hours, which would
    otherwise multiply noise by thirty. The elapsed time is floored at one hour
    to keep the arithmetic finite, and the result is marked low confidence.
    """
    elapsed = max(as_of - period_start, MIN_ELAPSED)
    elapsed_days = elapsed.total_seconds() / 86400.0
    total_days = (period_end - period_start).total_seconds() / 86400.0

    daily_run_rate = (spend_to_date / Decimal(str(elapsed_days))).quantize(Decimal("0.00000001"))
    projected = (daily_run_rate * Decimal(str(total_days))).quantize(CENT)

    utilization: float | None = None
    if budget_limit is not None and budget_limit > ZERO:
        utilization = round(float(projected / budget_limit), 4)

    return Forecast(
        team=team,
        period_start=period_start,
        period_end=period_end,
        as_of=as_of,
        spend_to_date=spend_to_date.quantize(CENT),
        elapsed_days=round(elapsed_days, 3),
        daily_run_rate=daily_run_rate.quantize(CENT),
        projected_total=projected,
        confidence=_confidence(elapsed_days),
        budget_limit=budget_limit,
        projected_utilization=utilization,
        breach_date=_breach_date(as_of, period_end, spend_to_date, daily_run_rate, budget_limit),
    )
