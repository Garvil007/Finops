"""Tests for the deterministic hourly simulation helpers."""

from datetime import UTC, datetime, timedelta

from finopsai.demo.simulation import (
    BUSINESS_HOURS_MULTIPLIER,
    demand_multiplier,
    floor_hour,
    hourly_buckets,
    seeded_rng,
)

MONDAY_NOON = datetime(2026, 8, 10, 12, 34, 56, tzinfo=UTC)
SATURDAY_NOON = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
MONDAY_MIDNIGHT = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)


def test_floor_hour_truncates_to_the_top_of_the_hour() -> None:
    # Assert
    assert floor_hour(MONDAY_NOON) == datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def test_naive_timestamps_are_treated_as_utc() -> None:
    # Assert
    assert floor_hour(datetime(2026, 8, 10, 12, 30)) == datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def test_buckets_cover_the_half_open_interval() -> None:
    # Act
    buckets = list(hourly_buckets(MONDAY_NOON, MONDAY_NOON + timedelta(hours=3)))

    # Assert: the closing hour is excluded because it is still in progress
    assert buckets == [
        datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        datetime(2026, 8, 10, 13, 0, tzinfo=UTC),
        datetime(2026, 8, 10, 14, 0, tzinfo=UTC),
    ]


def test_a_day_has_twenty_four_buckets() -> None:
    # Assert
    assert len(list(hourly_buckets(MONDAY_NOON, MONDAY_NOON + timedelta(days=1)))) == 24


def test_empty_when_the_window_is_inverted() -> None:
    # Assert
    assert list(hourly_buckets(MONDAY_NOON, MONDAY_NOON - timedelta(hours=5))) == []


def test_seeding_is_reproducible_for_the_same_parts() -> None:
    # Act
    first = seeded_rng("mock_compute", "research", "2026-08-10T12:00:00+00:00").random()
    second = seeded_rng("mock_compute", "research", "2026-08-10T12:00:00+00:00").random()

    # Assert
    assert first == second


def test_seeding_differs_across_parts() -> None:
    # Assert
    assert seeded_rng("a", 1).random() != seeded_rng("a", 2).random()


def test_business_hours_raise_demand_and_weekends_lower_it() -> None:
    # Assert
    assert demand_multiplier(floor_hour(MONDAY_NOON)) == BUSINESS_HOURS_MULTIPLIER
    assert demand_multiplier(floor_hour(MONDAY_MIDNIGHT)) == 1.0
    assert demand_multiplier(floor_hour(SATURDAY_NOON)) < 1.0
