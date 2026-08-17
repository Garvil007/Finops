"""Alert state: what has already been announced.

The rule is fire-once-per-crossing. Crossing 80% should alert once, not once
every fifteen minutes for the rest of the month, and crossing 100% afterwards
should alert again because the situation genuinely changed.

State is therefore the *highest threshold already announced* for one budget in
one period. A new threshold fires only when it exceeds that high-water mark.
The period key carries the month, so a new month starts from nothing without a
reset job to run or forget.

Redis holds it in production. The in-memory store exists so the state machine
can be tested without a broker, and so a Redis outage degrades to
alert-every-cycle rather than crash.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from redis.asyncio import Redis

from finopsai.logging import get_logger

log = get_logger(__name__)

KEY_PREFIX = "finopsai:alert"
# Two months, so a key outlives the period it belongs to but does not accumulate.
TTL_SECONDS = 60 * 60 * 24 * 62


def period_key(moment: datetime, period: str = "monthly") -> str:
    """Identify the budget period a moment falls in.

    The month is part of the key rather than something a scheduled job clears,
    so rollover cannot be missed.
    """
    if period == "daily":
        return moment.strftime("%Y-%m-%d")
    if period == "weekly":
        year, week, _ = moment.isocalendar()
        return f"{year}-W{week:02d}"
    return moment.strftime("%Y-%m")


@runtime_checkable
class AlertStateStore(Protocol):
    """Remembers the highest threshold already announced."""

    async def highest_fired(self, budget_id: int, period: str) -> int | None:
        """Highest threshold announced for this budget in this period."""
        ...

    async def record_fired(self, budget_id: int, period: str, threshold: int) -> None:
        """Remember that a threshold was announced."""
        ...


class InMemoryAlertState:
    """Process-local state. Fine for tests, useless across restarts."""

    def __init__(self) -> None:
        self._fired: dict[tuple[int, str], int] = {}

    async def highest_fired(self, budget_id: int, period: str) -> int | None:
        """Highest threshold announced, if any."""
        return self._fired.get((budget_id, period))

    async def record_fired(self, budget_id: int, period: str, threshold: int) -> None:
        """Raise the high-water mark."""
        current = self._fired.get((budget_id, period))
        if current is None or threshold > current:
            self._fired[(budget_id, period)] = threshold


class RedisAlertState:
    """Shared state, so restarts and multiple workers do not re-announce."""

    def __init__(self, client: Redis, ttl_seconds: int = TTL_SECONDS) -> None:
        self._client = client
        self._ttl = ttl_seconds

    @staticmethod
    def _key(budget_id: int, period: str) -> str:
        return f"{KEY_PREFIX}:{budget_id}:{period}"

    async def highest_fired(self, budget_id: int, period: str) -> int | None:
        """Read the high-water mark, treating an outage as nothing announced."""
        try:
            raw = await self._client.get(self._key(budget_id, period))
        except Exception as error:  # noqa: BLE001 - alerting must survive a broker outage
            log.warning("alert_state_read_failed", budget_id=budget_id, error=str(error))
            return None

        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            log.warning("alert_state_corrupt", budget_id=budget_id, value=str(raw))
            return None

    async def record_fired(self, budget_id: int, period: str, threshold: int) -> None:
        """Raise the high-water mark, keeping the larger of the two values."""
        key = self._key(budget_id, period)
        try:
            current = await self.highest_fired(budget_id, period)
            if current is None or threshold > current:
                await self._client.set(key, threshold, ex=self._ttl)
        except Exception as error:  # noqa: BLE001 - see above
            log.warning("alert_state_write_failed", budget_id=budget_id, error=str(error))
