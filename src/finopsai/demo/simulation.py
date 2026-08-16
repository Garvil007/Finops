"""Deterministic hourly simulation shared by the mock collectors.

Two properties make simulated spend safe to re-run:

* the dedup key is derived from the hour bucket, so a replay cannot insert the
  same hour twice;
* the amount is drawn from a generator seeded by that same bucket, so a replay
  produces the *same* number rather than a new random one.

Seeding uses a SHA-256 digest rather than :func:`hash`, whose string hashing is
randomised per process and would break reproducibility across runs.
"""

import hashlib
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

HOUR = timedelta(hours=1)
BUSINESS_HOURS = range(8, 19)
BUSINESS_HOURS_MULTIPLIER = 1.6
WEEKEND_MULTIPLIER = 0.45
SATURDAY = 5


def floor_hour(moment: datetime) -> datetime:
    """Truncate a timestamp to the top of its hour, in UTC."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def hourly_buckets(since: datetime, until: datetime) -> Iterator[datetime]:
    """Yield every whole hour in the half-open interval since..until."""
    bucket = floor_hour(since)
    end = floor_hour(until)
    while bucket < end:
        yield bucket
        bucket += HOUR


def seeded_rng(*parts: object) -> random.Random:
    """Return a generator seeded reproducibly from the given parts."""
    joined = "|".join(str(part) for part in parts)
    seed = int.from_bytes(hashlib.sha256(joined.encode("utf-8")).digest()[:8], "big")
    return random.Random(seed)


def demand_multiplier(bucket: datetime) -> float:
    """Scale usage by time of day and day of week so trend lines look real."""
    multiplier = 1.0
    if bucket.hour in BUSINESS_HOURS:
        multiplier *= BUSINESS_HOURS_MULTIPLIER
    if bucket.weekday() >= SATURDAY:
        multiplier *= WEEKEND_MULTIPLIER
    return multiplier
