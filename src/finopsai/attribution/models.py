"""SQLAlchemy models for the ``finops`` schema.

``CostRecord`` is the single fact table every collector writes into: one row is
one unit of spend from one source, already attributed to an owner. Keeping the
dimensions denormalised on the row makes the dashboards a plain ``GROUP BY``
and keeps collectors independent of one another.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from finopsai.attribution.tags import UNATTRIBUTED

SCHEMA = "finops"

# Money is Numeric, never float: float rounding corrupts cost totals at scale.
MONEY = Numeric(18, 8)
QUANTITY = Numeric(20, 4)


class Base(DeclarativeBase):
    """Declarative base pinned to the finops schema."""

    metadata = MetaData(schema=SCHEMA)


class CostSource(StrEnum):
    """Where a unit of spend came from."""

    LLM = "llm"
    COMPUTE = "compute"
    VECTORDB = "vectordb"
    INFRA = "infra"


class CostRecord(Base):
    """One attributed unit of spend from one source."""

    __tablename__ = "cost_record"
    __table_args__ = (
        # The dedup guarantee: a source may report the same event repeatedly
        # (retries, overlapping watermark windows) and it lands exactly once.
        UniqueConstraint("source", "dedup_key", name="uq_cost_record_source_dedup_key"),
        Index("ix_cost_record_occurred_at_team", "occurred_at", "team"),
        Index("ix_cost_record_source_occurred_at", "source", "occurred_at"),
        Index("ix_cost_record_allocated", "allocated"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    source: Mapped[CostSource] = mapped_column(
        Enum(CostSource, native_enum=False, length=16, validate_strings=True)
    )
    dedup_key: Mapped[str] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, default=None)
    unit: Mapped[str | None] = mapped_column(String(32), default=None)
    model: Mapped[str | None] = mapped_column(String(255), default=None)

    team: Mapped[str] = mapped_column(String(128), default=UNATTRIBUTED)
    project: Mapped[str] = mapped_column(String(128), default=UNATTRIBUTED)
    agent_id: Mapped[str] = mapped_column(String(128), default=UNATTRIBUTED)
    use_case: Mapped[str] = mapped_column(String(128), default=UNATTRIBUTED)

    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Allocation of shared cost keeps both sides of the split. The parent is
    # marked allocated and stops counting toward totals; the children carry the
    # per-team amounts and point back at it. Nothing is ever overwritten, so a
    # cost figure can always be traced to the record it came from.
    allocated: Mapped[bool] = mapped_column(Boolean, default=False)
    allocation_parent_id: Mapped[int | None] = mapped_column(Integer, default=None)
    allocation_rule: Mapped[str | None] = mapped_column(String(64), default=None)

    @property
    def is_allocation_child(self) -> bool:
        """True when this record was produced by splitting a shared cost."""
        return self.allocation_parent_id is not None

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CollectorWatermark(Base):
    """How far through its source a collector has read."""

    __tablename__ = "collector_watermark"

    collector: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_cursor: Mapped[str | None] = mapped_column(String(255), default=None)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Team(Base):
    """A cost owner. Rows here are the teams budgets and reports refer to."""

    __tablename__ = "team"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    slack_channel: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetPeriod(StrEnum):
    """How often a budget resets."""

    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"


class Budget(Base):
    """A spend ceiling for one team over one recurring period."""

    __tablename__ = "budget"
    __table_args__ = (UniqueConstraint("team", "period", name="uq_budget_team_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team: Mapped[str] = mapped_column(String(128))
    period: Mapped[BudgetPeriod] = mapped_column(
        Enum(BudgetPeriod, native_enum=False, length=16, validate_strings=True),
        default=BudgetPeriod.MONTHLY,
    )
    limit_usd: Mapped[Decimal] = mapped_column(MONEY)
    # Percentages of the limit at which an alert fires.
    alert_thresholds: Mapped[list[int]] = mapped_column(JSON, default=lambda: [80, 100])
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
