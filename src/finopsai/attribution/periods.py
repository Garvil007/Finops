"""Relative period parsing and comparison windows.

The API speaks in relative windows -- ``30d``, ``24h``, ``12w`` -- because that
is what a dashboard query string carries. Everything downstream works in
absolute half-open intervals, so parsing happens once, here.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from finopsai.attribution.engine import Period

PERIOD_PATTERN = re.compile(r"^(?P<size>\d+)(?P<unit>[hdw])$")

UNIT_TO_DELTA = {
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
}

MAX_UNITS = 3650


class Interval(StrEnum):
    """Bucket width for a timeseries."""

    HOUR = "1h"
    DAY = "1d"
    WEEK = "1w"


INTERVAL_TO_DELTA = {
    Interval.HOUR: timedelta(hours=1),
    Interval.DAY: timedelta(days=1),
    Interval.WEEK: timedelta(weeks=1),
}


class InvalidPeriodError(ValueError):
    """Raised when a period string cannot be parsed."""

    def __init__(self, value: str) -> None:
        super().__init__(
            f"invalid period {value!r}; expected a number followed by h, d or w (e.g. 30d)"
        )


@dataclass(frozen=True)
class PeriodPair:
    """A window and the equally sized window immediately before it."""

    current: Period
    previous: Period
    label: str


def parse_period(value: str) -> timedelta:
    """Turn a relative period such as ``30d`` into a duration."""
    match = PERIOD_PATTERN.match(value.strip().lower())
    if match is None:
        raise InvalidPeriodError(value)

    size = int(match.group("size"))
    if size <= 0 or size > MAX_UNITS:
        raise InvalidPeriodError(value)

    return size * UNIT_TO_DELTA[match.group("unit")]


def resolve_period(value: str, now: datetime | None = None) -> PeriodPair:
    """Resolve a relative period into the current window and its predecessor.

    The comparison window is the same length immediately before the current one,
    so a 30 day view is compared with the 30 days before it rather than with a
    calendar month of a different length.
    """
    end = now or datetime.now(UTC)
    duration = parse_period(value)
    current = Period(start=end - duration, end=end)
    previous = Period(start=current.start - duration, end=current.start)
    return PeriodPair(current=current, previous=previous, label=value.strip().lower())


def change_percent(current: Decimal | float, previous: Decimal | float) -> float | None:
    """Percentage change between two totals.

    Returns ``None`` when there is no baseline: growth from zero is not a
    percentage, and reporting it as infinite or as 100% would both be lies.
    """
    if previous == 0:
        return None
    return round(((float(current) - float(previous)) / float(previous)) * 100.0, 2)
