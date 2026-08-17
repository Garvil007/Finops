"""Alert payloads and the notifier contract.

Kept separate from both the evaluator and the Slack sender so neither has to
import the other: the evaluator produces alerts, a notifier consumes them, and
swapping the channel is a constructor argument.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from finopsai.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class CostDriver:
    """One of the things pushing a team's spend up."""

    label: str
    amount_usd: Decimal


@dataclass(frozen=True)
class BudgetAlert:
    """A budget threshold crossing, with the context needed to act on it."""

    team: str
    budget_id: int
    threshold: int
    period_key: str
    period_start: datetime
    period_end: datetime
    spend_to_date: Decimal
    budget_limit: Decimal
    utilization: float
    projected_total: Decimal
    breach_date: datetime | None = None
    drivers: Sequence[CostDriver] = field(default_factory=tuple)
    dashboard_url: str | None = None

    @property
    def is_exhausted(self) -> bool:
        """True once the budget is fully spent."""
        return self.threshold >= 100

    @property
    def remaining_usd(self) -> Decimal:
        """Budget left, floored at zero."""
        return max(self.budget_limit - self.spend_to_date, Decimal(0))


@runtime_checkable
class Notifier(Protocol):
    """Anything that can deliver a budget alert."""

    async def send(self, alert: BudgetAlert) -> bool:
        """Deliver the alert. Returns True when it was actually sent."""
        ...


class NullNotifier:
    """Logs instead of sending.

    Used when no webhook is configured. An unconfigured alerting path should be
    visible in the logs, not silently dropped -- a budget alert nobody receives
    is worse than no alerting at all, because it looks like it works.
    """

    async def send(self, alert: BudgetAlert) -> bool:
        """Record that an alert would have been sent."""
        log.warning(
            "alert_not_delivered_no_notifier_configured",
            team=alert.team,
            threshold=alert.threshold,
            utilization=round(alert.utilization, 4),
            spend_usd=str(alert.spend_to_date),
            budget_usd=str(alert.budget_limit),
        )
        return False


class CollectingNotifier:
    """Keeps alerts in memory. For tests and dry runs."""

    def __init__(self) -> None:
        self.sent: list[BudgetAlert] = []

    async def send(self, alert: BudgetAlert) -> bool:
        """Record the alert."""
        self.sent.append(alert)
        return True
