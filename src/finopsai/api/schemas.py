"""Pydantic request and response models.

Every response carries an example so the generated OpenAPI page is readable on
its own. The docs page is part of what this project demonstrates, so it is
treated as an interface, not a by-product.

Money is serialised as a JSON number produced from ``Decimal``. It is never
computed as a float: the arithmetic happens in the database and in ``Decimal``
all the way to serialisation.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from finopsai.attribution.forecast import ForecastConfidence
from finopsai.attribution.models import BudgetPeriod


class PeriodModel(BaseModel):
    """A half-open time window."""

    start: datetime
    end: datetime
    label: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "start": "2026-07-17T00:00:00Z",
                "end": "2026-08-16T00:00:00Z",
                "label": "30d",
            }
        }
    )


class CostGroupModel(BaseModel):
    """One grouped total, with its per-source split and period comparison."""

    key: dict[str, str]
    total_usd: Decimal
    by_source: dict[str, Decimal]
    record_count: int
    previous_total_usd: Decimal | None = None
    change_percent: float | None = Field(
        default=None,
        description="Change against the preceding window. Null when the baseline is zero.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "key": {"team": "research"},
                "total_usd": 1284.42,
                "by_source": {"llm": 412.10, "compute": 802.32, "vectordb": 70.00},
                "record_count": 1043,
                "previous_total_usd": 902.11,
                "change_percent": 42.38,
            }
        }
    )


class CostQueryResponse(BaseModel):
    """Grouped spend for a window, compared with the preceding one."""

    period: PeriodModel
    comparison_period: PeriodModel
    total_usd: Decimal
    previous_total_usd: Decimal
    change_percent: float | None
    groups: list[CostGroupModel]


class UnattributedSliceModel(BaseModel):
    """One contributor to spend that has no owner."""

    label: str
    amount_usd: Decimal


class UnattributedResponse(BaseModel):
    """The go-fix-your-tagging report."""

    period: PeriodModel
    total_usd: Decimal
    unattributed_usd: Decimal
    attributed_usd: Decimal
    unattributed_share: float = Field(description="Fraction between 0 and 1.")
    by_source: dict[str, Decimal]
    top_models: list[UnattributedSliceModel]
    top_resources: list[UnattributedSliceModel]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "period": {"start": "2026-07-17T00:00:00Z", "end": "2026-08-16T00:00:00Z"},
                "total_usd": 4820.55,
                "unattributed_usd": 611.20,
                "attributed_usd": 4209.35,
                "unattributed_share": 0.1268,
                "by_source": {"llm": 480.00, "vectordb": 131.20},
                "top_models": [{"label": "gpt-4o-mini", "amount_usd": 402.11}],
                "top_resources": [{"label": "discovery", "amount_usd": 131.20}],
            }
        }
    )


class TimeseriesPointModel(BaseModel):
    """One bucket of one series."""

    bucket: datetime
    key: dict[str, str]
    amount_usd: Decimal


class TimeseriesResponse(BaseModel):
    """Bucketed spend, shaped for a dashboard."""

    period: PeriodModel
    interval: str
    points: list[TimeseriesPointModel]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "period": {"start": "2026-08-09T00:00:00Z", "end": "2026-08-16T00:00:00Z"},
                "interval": "1d",
                "points": [
                    {
                        "bucket": "2026-08-09T00:00:00Z",
                        "key": {"team": "search"},
                        "amount_usd": 61.20,
                    }
                ],
            }
        }
    )


class TeamCreate(BaseModel):
    """Register a cost owner."""

    name: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)
    slack_channel: str | None = Field(default=None, max_length=128)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "search",
                "display_name": "Search Platform",
                "slack_channel": "#search-alerts",
            }
        }
    )


class TeamModel(BaseModel):
    """A registered cost owner."""

    name: str
    display_name: str | None = None
    slack_channel: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BudgetCreate(BaseModel):
    """Set a spend ceiling for a team."""

    team: str = Field(min_length=1, max_length=128)
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    limit_usd: Decimal = Field(gt=0)
    alert_thresholds: list[int] = Field(default_factory=lambda: [80, 100])
    is_active: bool = True

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "team": "research",
                "period": "monthly",
                "limit_usd": 2000.00,
                "alert_thresholds": [80, 100],
                "is_active": True,
            }
        }
    )


class BudgetUpdate(BaseModel):
    """Change a budget. Omitted fields are left alone."""

    limit_usd: Decimal | None = Field(default=None, gt=0)
    alert_thresholds: list[int] | None = None
    is_active: bool | None = None


class BudgetModel(BaseModel):
    """A stored budget."""

    id: int
    team: str
    period: BudgetPeriod
    limit_usd: Decimal
    alert_thresholds: list[int]
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ForecastResponse(BaseModel):
    """Straight-line projection of the current period."""

    team: str
    period_start: datetime
    period_end: datetime
    as_of: datetime
    spend_to_date: Decimal
    elapsed_days: float
    daily_run_rate: Decimal
    projected_total: Decimal
    confidence: ForecastConfidence
    budget_limit: Decimal | None = None
    projected_utilization: float | None = None
    breach_date: datetime | None = None
    will_breach: bool
    method: str = Field(
        default="linear_run_rate",
        description="MVP projection. A v2 would use Holt-Winters or Prophet for seasonality.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "team": "research",
                "period_start": "2026-08-01T00:00:00Z",
                "period_end": "2026-09-01T00:00:00Z",
                "as_of": "2026-08-16T12:00:00Z",
                "spend_to_date": 1180.44,
                "elapsed_days": 15.5,
                "daily_run_rate": 76.16,
                "projected_total": 2360.88,
                "confidence": "high",
                "budget_limit": 2000.00,
                "projected_utilization": 1.1804,
                "breach_date": "2026-08-26T18:24:00Z",
                "will_breach": True,
                "method": "linear_run_rate",
            }
        }
    )


class HealthResponse(BaseModel):
    """Liveness and readiness."""

    status: str
    checks: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(
        json_schema_extra={"example": {"status": "ok", "checks": {"database": "ok"}}}
    )


class ErrorResponse(BaseModel):
    """A failed request."""

    detail: str

    model_config = ConfigDict(json_schema_extra={"example": {"detail": "unknown dimension 'foo'"}})
