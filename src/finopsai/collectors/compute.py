"""Compute spend collector.

The MVP fabricates plausible per-team, per-hour compute cost so the dashboard
can show that infrastructure is a real share of AI spend rather than a rounding
error next to tokens. Every record it writes carries a simulated marker in
``raw`` and a ``mock-compute:`` dedup key, so simulated spend can never be
mistaken for measured spend.

Replacing this with the real thing means writing an ``AWSCostExplorerCollector``
with the same :class:`~finopsai.collectors.base.BaseCollector` interface -- same
``collect()`` signature, same ``next_watermark()`` -- and swapping it in
:func:`finopsai.collectors.runner.build_collectors`. The call it would make::

    client = boto3.client("ce")
    client.get_cost_and_usage(
        TimePeriod={"Start": "2026-08-01", "End": "2026-08-15"},
        Granularity="DAILY",
        Metrics=["UnblendedCost", "UsageQuantity"],
        GroupBy=[
            {"Type": "DIMENSION", "Key": "SERVICE"},
            {"Type": "TAG", "Key": "team"},
        ],
        Filter={"Tags": {"Key": "team", "Values": ["search", "support-bot", "research"]}},
    )

Three caveats shape the real implementation, none of which the mock has:

* cost allocation tags must be activated in the Billing console before they
  appear as a GroupBy key, and activation is not retroactive;
* HOURLY granularity requires resource-level data to be enabled, so the real
  collector is daily while this mock is hourly;
* every get_cost_and_usage request is billed, so the real collector must respect
  its watermark rather than re-reading history each cycle.

This block is written from documentation, not from a verified run against an AWS
account.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.attribution.models import CostRecord, CostSource
from finopsai.collectors.base import BaseCollector, Watermark
from finopsai.demo.simulation import demand_multiplier, hourly_buckets, seeded_rng

GPU_HOUR = "gpu-hour"
VCPU_HOUR = "vcpu-hour"
CENT = Decimal("0.00000001")
DEFAULT_BACKFILL_DAYS = 14
MAX_BUCKETS_PER_CYCLE = 24 * 30
INTERVAL_SECONDS = 300.0


@dataclass(frozen=True)
class ComputeProfile:
    """Simulated compute footprint for one team."""

    team: str
    project: str
    use_case: str
    unit: str
    units_per_hour: tuple[float, float]
    usd_per_unit: Decimal


# Research fine-tunes on GPUs, which is why its infrastructure bill dwarfs its
# token bill -- the exact inversion FinOpsAI exists to make visible.
COMPUTE_PROFILES: tuple[ComputeProfile, ...] = (
    ComputeProfile(
        team="research",
        project="model-lab",
        use_case="fine-tuning",
        unit=GPU_HOUR,
        units_per_hour=(0.6, 2.4),
        usd_per_unit=Decimal("12.2400"),
    ),
    ComputeProfile(
        team="support-bot",
        project="customer-support",
        use_case="inference-serving",
        unit=VCPU_HOUR,
        units_per_hour=(6.0, 18.0),
        usd_per_unit=Decimal("0.0416"),
    ),
    ComputeProfile(
        team="search",
        project="discovery",
        use_case="inference-serving",
        unit=VCPU_HOUR,
        units_per_hour=(3.0, 11.0),
        usd_per_unit=Decimal("0.0416"),
    ),
)

PROFILES_BY_TEAM: Mapping[str, ComputeProfile] = {p.team: p for p in COMPUTE_PROFILES}


class MockComputeCollector(BaseCollector):
    """Generates simulated compute spend per team per hour."""

    name: ClassVar[str] = "mock_compute"
    source: ClassVar[CostSource] = CostSource.COMPUTE
    interval_seconds: ClassVar[float] = INTERVAL_SECONDS

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        backfill_days: int = DEFAULT_BACKFILL_DAYS,
        profiles: tuple[ComputeProfile, ...] = COMPUTE_PROFILES,
    ) -> None:
        super().__init__(sessions)
        self._backfill_days = backfill_days
        self._profiles = profiles

    async def collect(self) -> list[CostRecord]:
        """Emit one record per team per completed hour since the watermark."""
        watermark = await self.read_watermark()
        now = datetime.now(UTC)
        since = watermark.last_occurred_at or (now - timedelta(days=self._backfill_days))

        records: list[CostRecord] = []
        for index, bucket in enumerate(hourly_buckets(since, now)):
            if index >= MAX_BUCKETS_PER_CYCLE:
                self._log.info("backfill_truncated", limit=MAX_BUCKETS_PER_CYCLE)
                break
            records.extend(self._build_record(profile, bucket) for profile in self._profiles)
        return records

    def next_watermark(self, records: list[CostRecord]) -> Watermark | None:
        """Advance to the newest hour generated."""
        if not records:
            return None
        newest = max(record.occurred_at for record in records)
        return Watermark(last_occurred_at=newest, last_cursor=newest.isoformat())

    def _build_record(self, profile: ComputeProfile, bucket: datetime) -> CostRecord:
        """Draw a reproducible cost for one team-hour."""
        rng = seeded_rng(self.name, profile.team, bucket.isoformat())
        low, high = profile.units_per_hour
        units = Decimal(str(round(rng.uniform(low, high) * demand_multiplier(bucket), 4)))
        amount = (units * profile.usd_per_unit).quantize(CENT)

        return CostRecord(
            source=CostSource.COMPUTE,
            dedup_key=f"mock-compute:{profile.team}:{bucket.isoformat()}",
            occurred_at=bucket,
            amount_usd=amount,
            quantity=units,
            unit=profile.unit,
            model=None,
            team=profile.team,
            project=profile.project,
            agent_id="infrastructure",
            use_case=profile.use_case,
            raw={"simulated": True, "usd_per_unit": str(profile.usd_per_unit)},
        )
