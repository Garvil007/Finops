"""Abstract collector contract and the supervised run loop.

A collector reads one cost source and returns ``CostRecord`` rows. Everything
else -- deduplication, watermark bookkeeping, metrics, error isolation and
scheduling -- lives here so every source behaves identically.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.attribution.models import CollectorWatermark, CostRecord, CostSource
from finopsai.logging import get_logger
from finopsai.metrics import (
    COLLECTOR_RECORDS,
    COLLECTOR_RUNS,
    STATUS_ERROR,
    STATUS_SUCCESS,
)

DEFAULT_INTERVAL_SECONDS = 300.0


@dataclass(frozen=True)
class Watermark:
    """How far a collector has read through its source."""

    last_occurred_at: datetime | None = None
    last_cursor: str | None = None


class BaseCollector(ABC):
    """Reads one cost source on an interval and writes attributed records."""

    name: ClassVar[str]
    source: ClassVar[CostSource]
    interval_seconds: ClassVar[float] = DEFAULT_INTERVAL_SECONDS

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._log = get_logger(__name__).bind(collector=self.name)

    @abstractmethod
    async def collect(self) -> list[CostRecord]:
        """Read new spend from the source and map it to cost records."""

    def next_watermark(self, records: list[CostRecord]) -> Watermark | None:
        """Watermark to store once these records are committed."""
        return None

    async def read_watermark(self) -> Watermark:
        """Load this collector's stored position, or an empty one."""
        async with self._sessions() as session:
            stored = await session.get(CollectorWatermark, self.name)
            if stored is None:
                return Watermark()
            return Watermark(
                last_occurred_at=stored.last_occurred_at,
                last_cursor=stored.last_cursor,
            )

    async def persist(self, records: list[CostRecord]) -> int:
        """Write new records and the watermark in one transaction.

        Records already present are dropped by dedup key, so an overlapping
        read window or a replayed batch cannot double-count spend.
        """
        watermark = self.next_watermark(records)

        async with self._sessions() as session, session.begin():
            fresh = await self._drop_duplicates(session, records)
            session.add_all(fresh)
            await self._store_watermark(session, watermark)

        return len(fresh)

    async def run_once(self) -> int:
        """Run a single cycle. Never raises: a bad cycle is logged and counted.

        Persistence and watermark advance share a transaction, so a crash
        mid-cycle simply re-reads the same window next time and the dedup key
        absorbs the overlap.
        """
        try:
            records = await self.collect()
            written = await self.persist(records)
        except Exception:
            COLLECTOR_RUNS.labels(collector=self.name, status=STATUS_ERROR).inc()
            self._log.exception("collector_cycle_failed")
            return 0

        COLLECTOR_RUNS.labels(collector=self.name, status=STATUS_SUCCESS).inc()
        COLLECTOR_RECORDS.labels(collector=self.name).inc(written)
        self._log.info(
            "collector_cycle_complete",
            collected=len(records),
            written=written,
            duplicates=len(records) - written,
        )
        return written

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Run cycles on the configured interval until stopped or cancelled."""
        self._log.info("collector_started", interval_seconds=self.interval_seconds)
        while stop is None or not stop.is_set():
            await self.run_once()
            if stop is None:
                await asyncio.sleep(self.interval_seconds)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue
        self._log.info("collector_stopped")

    async def _drop_duplicates(
        self, session: AsyncSession, records: list[CostRecord]
    ) -> list[CostRecord]:
        """Filter out records whose dedup key is already stored."""
        keys = [record.dedup_key for record in records]
        if not keys:
            return []

        seen = set(
            (
                await session.scalars(
                    select(CostRecord.dedup_key).where(
                        CostRecord.source == self.source,
                        CostRecord.dedup_key.in_(keys),
                    )
                )
            ).all()
        )

        fresh: list[CostRecord] = []
        for record in records:
            if record.dedup_key in seen:
                continue
            seen.add(record.dedup_key)
            fresh.append(record)
        return fresh

    async def _store_watermark(self, session: AsyncSession, watermark: Watermark | None) -> None:
        """Insert or update this collector's watermark row."""
        stored = await session.get(CollectorWatermark, self.name)
        if stored is None:
            stored = CollectorWatermark(collector=self.name)
            session.add(stored)

        stored.last_run_at = datetime.now(UTC)
        if watermark is not None:
            stored.last_occurred_at = watermark.last_occurred_at
            stored.last_cursor = watermark.last_cursor
