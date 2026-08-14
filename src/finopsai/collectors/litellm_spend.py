"""Collector for LLM spend recorded by the LiteLLM proxy.

Reads ``LiteLLM_SpendLogs`` strictly read-only: that table is owned by the
proxy's Prisma migrations. Columns are listed explicitly rather than reflected
so a schema change upstream surfaces as a clear error instead of silently
altering what we ingest.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from finopsai.attribution.models import CostRecord, CostSource
from finopsai.attribution.tags import resolve_attribution
from finopsai.collectors.base import BaseCollector, Watermark

TOKENS = "tokens"
DEFAULT_BATCH_SIZE = 1000
DEFAULT_INTERVAL_SECONDS = 60.0

metadata_obj = sa.MetaData()

SPEND_LOGS = sa.Table(
    "LiteLLM_SpendLogs",
    metadata_obj,
    sa.Column("request_id", sa.String, primary_key=True),
    sa.Column("startTime", sa.DateTime(timezone=True)),
    sa.Column("model", sa.String),
    sa.Column("spend", sa.Float),
    sa.Column("total_tokens", sa.Integer),
    sa.Column("request_tags", sa.JSON),
    sa.Column("metadata", sa.JSON),
)


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    """Coerce a JSON column to a mapping, tolerating a serialized string."""
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, Mapping) else None
    return None


def _as_tag_list(value: Any) -> list[str]:
    """Coerce a JSON column to a list of tag strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [item for item in value if isinstance(item, str)]
    return []


def _as_utc(value: datetime | None) -> datetime:
    """Normalize a source timestamp to an aware UTC datetime."""
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class LiteLLMSpendCollector(BaseCollector):
    """Ingests LiteLLM spend logs into the cost warehouse."""

    name: ClassVar[str] = "litellm_spend"
    source: ClassVar[CostSource] = CostSource.LLM
    interval_seconds: ClassVar[float] = DEFAULT_INTERVAL_SECONDS

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        litellm_engine: AsyncEngine,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        super().__init__(sessions)
        self._litellm_engine = litellm_engine
        self._batch_size = batch_size

    async def collect(self) -> list[CostRecord]:
        """Read spend logs at or after the stored watermark."""
        watermark = await self.read_watermark()
        rows = await self._fetch_rows(watermark.last_occurred_at)
        self._log.debug("spend_logs_fetched", rows=len(rows), since=watermark.last_occurred_at)
        return [self._to_record(row) for row in rows]

    def next_watermark(self, records: list[CostRecord]) -> Watermark | None:
        """Advance to the newest row seen in this batch."""
        if not records:
            return None
        newest = max(records, key=lambda record: record.occurred_at)
        return Watermark(
            last_occurred_at=newest.occurred_at,
            last_cursor=newest.dedup_key,
        )

    async def _fetch_rows(self, since: datetime | None) -> Sequence[Row[Any]]:
        """Select the next batch of spend logs.

        The window is inclusive of the watermark instant: several rows can share
        a timestamp, and re-reading the boundary costs nothing because the dedup
        key drops what is already stored. Excluding it would lose spend.
        """
        query = sa.select(
            SPEND_LOGS.c.request_id,
            SPEND_LOGS.c["startTime"],
            SPEND_LOGS.c.model,
            SPEND_LOGS.c.spend,
            SPEND_LOGS.c.total_tokens,
            SPEND_LOGS.c.request_tags,
            SPEND_LOGS.c["metadata"],
        ).order_by(SPEND_LOGS.c["startTime"].asc())

        if since is not None:
            query = query.where(SPEND_LOGS.c["startTime"] >= since)

        query = query.limit(self._batch_size)

        async with self._litellm_engine.connect() as connection:
            result = await connection.execute(query)
            return result.all()

    def _to_record(self, row: Row[Any]) -> CostRecord:
        """Map one spend log to an attributed cost record."""
        source_metadata = _as_mapping(row.metadata)
        tags = _as_tag_list(row.request_tags)
        attribution = resolve_attribution(tags, source_metadata)
        occurred_at = _as_utc(row.startTime)

        return CostRecord(
            source=CostSource.LLM,
            dedup_key=row.request_id,
            occurred_at=occurred_at,
            amount_usd=Decimal(str(row.spend or 0)),
            quantity=Decimal(row.total_tokens or 0),
            unit=TOKENS,
            model=row.model,
            team=attribution.team,
            project=attribution.project,
            agent_id=attribution.agent_id,
            use_case=attribution.use_case,
            raw={"request_tags": tags},
        )
