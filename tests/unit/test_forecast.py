"""Tests for the run-rate forecast."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from finopsai.attribution.forecast import (
    ForecastConfidence,
    build_forecast,
    month_bounds,
)

MONTH_START = datetime(2026, 8, 1, tzinfo=UTC)
MONTH_END = datetime(2026, 9, 1, tzinfo=UTC)
MID_MONTH = datetime(2026, 8, 16, tzinfo=UTC)


def _forecast(spend: str, as_of: datetime = MID_MONTH, limit: str | None = None) -> object:
    return build_forecast(
        team="research",
        spend_to_date=Decimal(spend),
        as_of=as_of,
        period_start=MONTH_START,
        period_end=MONTH_END,
        budget_limit=Decimal(limit) if limit is not None else None,
    )


def test_month_bounds_wrap_the_year() -> None:
    # Assert
    assert month_bounds(datetime(2026, 12, 15, tzinfo=UTC)) == (
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_half_a_month_projects_to_roughly_double() -> None:
    # Act: 15 days elapsed of a 31 day month
    forecast = _forecast("1500.00")

    # Assert
    assert forecast.daily_run_rate == Decimal("100.00")
    assert forecast.projected_total == Decimal("3100.00")
    assert forecast.confidence is ForecastConfidence.HIGH


def test_zero_spend_projects_zero_and_never_breaches() -> None:
    # Act
    forecast = _forecast("0.00", limit="500.00")

    # Assert
    assert forecast.projected_total == Decimal("0.00")
    assert forecast.daily_run_rate == Decimal("0.00")
    assert forecast.breach_date is None
    assert forecast.will_breach is False


def test_first_hour_of_the_month_does_not_divide_by_zero() -> None:
    # Act: the month has barely started
    forecast = _forecast("5.00", as_of=MONTH_START)

    # Assert: elapsed time is floored, and the result is flagged as unreliable
    assert forecast.elapsed_days > 0
    assert forecast.projected_total > Decimal("0")
    assert forecast.confidence is ForecastConfidence.LOW


def test_day_one_is_low_confidence() -> None:
    # Act
    forecast = _forecast("40.00", as_of=MONTH_START + timedelta(hours=12))

    # Assert
    assert forecast.confidence is ForecastConfidence.LOW


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        pytest.param(MONTH_START + timedelta(days=1), ForecastConfidence.LOW, id="day-1"),
        pytest.param(MONTH_START + timedelta(days=5), ForecastConfidence.MEDIUM, id="day-5"),
        pytest.param(MONTH_START + timedelta(days=20), ForecastConfidence.HIGH, id="day-20"),
    ],
)
def test_confidence_grows_with_elapsed_time(as_of: datetime, expected: ForecastConfidence) -> None:
    assert _forecast("100.00", as_of=as_of).confidence is expected


def test_breach_date_lands_inside_the_month() -> None:
    # Act: 100/day against a 2000 limit, 1500 already spent
    forecast = _forecast("1500.00", limit="2000.00")

    # Assert: five days of headroom left
    assert forecast.will_breach is True
    assert forecast.breach_date is not None
    assert MID_MONTH < forecast.breach_date < MONTH_END
    assert forecast.breach_date.day == 21


def test_no_breach_when_the_run_rate_stays_under_budget() -> None:
    # Act
    forecast = _forecast("1500.00", limit="10000.00")

    # Assert: the limit is not reached before the month ends
    assert forecast.will_breach is False
    assert forecast.breach_date is None
    assert forecast.projected_utilization == 0.31


def test_already_over_budget_reports_a_breach_now() -> None:
    # Act
    forecast = _forecast("2500.00", limit="2000.00")

    # Assert
    assert forecast.will_breach is True
    assert forecast.breach_date == MID_MONTH


def test_no_budget_means_no_utilisation_or_breach() -> None:
    # Act
    forecast = _forecast("1500.00")

    # Assert
    assert forecast.budget_limit is None
    assert forecast.projected_utilization is None
    assert forecast.will_breach is False
