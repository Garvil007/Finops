"""Vector database spend collector.

The MVP fabricates Pinecone-shaped usage per index: read units, write units, and
stored data. Like the compute mock, every record carries a simulated marker in
``raw`` and a ``mock-vectordb:`` dedup key.

Costs are billed per project and index, not per team, which is why this collector
is keyed on the index. That mismatch is real and worth keeping visible: mapping a
vector-store bill back to the team that caused it is exactly the allocation
problem FinOpsAI solves.

Replacing this with the real thing means a ``PineconeUsageCollector`` with the
same interface, reading the usage endpoint per index and mapping index names to
projects. The mapping is the hard part -- serverless usage is reported per index
with no notion of our team dimension -- so the real collector needs an explicit
index-to-project lookup, and anything unmapped must land in ``unattributed``
rather than being silently dropped.

The rate card below is illustrative, not a quote.
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

READ_UNIT = "read-unit"
USD_PER_MILLION_READ_UNITS = Decimal("8.25")
USD_PER_MILLION_WRITE_UNITS = Decimal("2.00")
USD_PER_GB_HOUR = Decimal("0.00045")
MILLION = Decimal(1000000)
CENT = Decimal("0.00000001")

DEFAULT_BACKFILL_DAYS = 14
MAX_BUCKETS_PER_CYCLE = 24 * 30
INTERVAL_SECONDS = 300.0


@dataclass(frozen=True)
class IndexProfile:
    """Simulated vector index owned by one project."""

    index: str
    project: str
    team: str
    use_case: str
    read_units_per_hour: tuple[float, float]
    write_units_per_hour: tuple[float, float]
    stored_gb: float


INDEX_PROFILES: tuple[IndexProfile, ...] = (
    IndexProfile(
        index="support-kb",
        project="customer-support",
        team="support-bot",
        use_case="faq",
        read_units_per_hour=(90000.0, 260000.0),
        write_units_per_hour=(1000.0, 6000.0),
        stored_gb=14.0,
    ),
    IndexProfile(
        index="discovery-docs",
        project="discovery",
        team="search",
        use_case="rag",
        read_units_per_hour=(140000.0, 420000.0),
        write_units_per_hour=(4000.0, 15000.0),
        stored_gb=52.0,
    ),
    IndexProfile(
        index="research-corpus",
        project="model-lab",
        team="research",
        use_case="literature-review",
        read_units_per_hour=(8000.0, 30000.0),
        write_units_per_hour=(12000.0, 40000.0),
        stored_gb=96.0,
    ),
)

PROFILES_BY_INDEX: Mapping[str, IndexProfile] = {p.index: p for p in INDEX_PROFILES}


class MockVectorDBCollector(BaseCollector):
    """Generates simulated vector database spend per index per hour."""

    name: ClassVar[str] = "mock_vectordb"
    source: ClassVar[CostSource] = CostSource.VECTORDB
    interval_seconds: ClassVar[float] = INTERVAL_SECONDS

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        backfill_days: int = DEFAULT_BACKFILL_DAYS,
        profiles: tuple[IndexProfile, ...] = INDEX_PROFILES,
    ) -> None:
        super().__init__(sessions)
        self._backfill_days = backfill_days
        self._profiles = profiles

    async def collect(self) -> list[CostRecord]:
        """Emit one record per index per completed hour since the watermark."""
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

    def _build_record(self, profile: IndexProfile, bucket: datetime) -> CostRecord:
        """Draw a reproducible cost for one index-hour."""
        rng = seeded_rng(self.name, profile.index, bucket.isoformat())
        demand = demand_multiplier(bucket)

        reads = Decimal(str(round(rng.uniform(*profile.read_units_per_hour) * demand, 2)))
        writes = Decimal(str(round(rng.uniform(*profile.write_units_per_hour), 2)))

        amount = (
            reads / MILLION * USD_PER_MILLION_READ_UNITS
            + writes / MILLION * USD_PER_MILLION_WRITE_UNITS
            + Decimal(str(profile.stored_gb)) * USD_PER_GB_HOUR
        ).quantize(CENT)

        return CostRecord(
            source=CostSource.VECTORDB,
            dedup_key=f"mock-vectordb:{profile.index}:{bucket.isoformat()}",
            occurred_at=bucket,
            amount_usd=amount,
            quantity=reads,
            unit=READ_UNIT,
            model=None,
            team=profile.team,
            project=profile.project,
            agent_id="vector-index",
            use_case=profile.use_case,
            raw={
                "simulated": True,
                "index": profile.index,
                "write_units": str(writes),
                "stored_gb": profile.stored_gb,
            },
        )
