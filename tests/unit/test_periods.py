"""Tests for relative period parsing and comparison windows."""

from datetime import UTC, datetime, timedelta

import pytest

from finopsai.attribution.periods import (
    InvalidPeriodError,
    change_percent,
    parse_period,
    resolve_period,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("30d", timedelta(days=30), id="days"),
        pytest.param("24h", timedelta(hours=24), id="hours"),
        pytest.param("2w", timedelta(weeks=2), id="weeks"),
        pytest.param(" 7D ", timedelta(days=7), id="whitespace-and-case"),
    ],
)
def test_periods_parse(value: str, expected: timedelta) -> None:
    assert parse_period(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("30", id="no-unit"),
        pytest.param("d", id="no-size"),
        pytest.param("30y", id="unsupported-unit"),
        pytest.param("0d", id="zero"),
        pytest.param("-5d", id="negative"),
        pytest.param("99999d", id="absurd"),
        pytest.param("30d; drop table cost_record", id="injection-attempt"),
    ],
)
def test_invalid_periods_are_rejected(value: str) -> None:
    with pytest.raises(InvalidPeriodError):
        parse_period(value)


def test_comparison_window_is_the_same_length_immediately_before() -> None:
    # Act
    pair = resolve_period("30d", now=NOW)

    # Assert
    assert pair.current.end == NOW
    assert pair.current.start == NOW - timedelta(days=30)
    assert pair.previous.end == pair.current.start
    assert pair.previous.start == NOW - timedelta(days=60)
    assert pair.label == "30d"


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        pytest.param(150, 100, 50.0, id="growth"),
        pytest.param(50, 100, -50.0, id="decline"),
        pytest.param(100, 100, 0.0, id="flat"),
        pytest.param(0, 100, -100.0, id="stopped"),
    ],
)
def test_change_percent(current: int, previous: int, expected: float) -> None:
    assert change_percent(current, previous) == expected


def test_change_from_zero_is_undefined_not_infinite() -> None:
    # Growth from nothing is not a percentage; reporting one would be a lie.
    assert change_percent(100, 0) is None
