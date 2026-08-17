"""Slack delivery for budget alerts.

The message answers the three questions someone asks when a budget alert
arrives: how bad is it, what is driving it, and when does it run out. A bar
renders utilisation at a glance, the top drivers say where to look, and the
forecast line gives the deadline.
"""

from decimal import Decimal

import httpx

from finopsai.alerting.alerts import BudgetAlert
from finopsai.logging import get_logger

log = get_logger(__name__)

BAR_WIDTH = 20
BAR_FILLED = "\u2588"
BAR_EMPTY = "\u2591"
MAX_DRIVERS = 3
REQUEST_TIMEOUT_SECONDS = 10.0

WARNING_EMOJI = ":warning:"
EXHAUSTED_EMOJI = ":rotating_light:"


def utilization_bar(utilization: float, width: int = BAR_WIDTH) -> str:
    """Render utilisation as a text bar, clamped so overspend stays readable."""
    ratio = max(0.0, min(utilization, 1.0))
    filled = int(round(ratio * width))
    return BAR_FILLED * filled + BAR_EMPTY * (width - filled)


def _money(amount: Decimal) -> str:
    """Format an amount for a human, not for arithmetic."""
    return f"${amount:,.2f}"


def build_blocks(alert: BudgetAlert) -> list[dict[str, object]]:
    """Build the Block Kit payload for one alert."""
    emoji = EXHAUSTED_EMOJI if alert.is_exhausted else WARNING_EMOJI
    headline = "exhausted" if alert.is_exhausted else f"at {alert.threshold}%"

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} {alert.team} budget {headline}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"`{utilization_bar(alert.utilization)}` "
                    f"*{alert.utilization:.0%}*\n"
                    f"{_money(alert.spend_to_date)} of {_money(alert.budget_limit)} "
                    f"({_money(alert.remaining_usd)} left) - period {alert.period_key}"
                ),
            },
        },
    ]

    if alert.drivers:
        drivers = "\n".join(
            f"{index}. *{driver.label}* - {_money(driver.amount_usd)}"
            for index, driver in enumerate(alert.drivers[:MAX_DRIVERS], start=1)
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Top cost drivers*\n{drivers}"},
            }
        )

    if alert.breach_date is not None:
        forecast_line = (
            f"At the current run rate the budget is exhausted on "
            f"*{alert.breach_date:%b %d}*, projecting "
            f"{_money(alert.projected_total)} for the period."
        )
    else:
        forecast_line = (
            f"At the current run rate the period ends at "
            f"{_money(alert.projected_total)}, within budget."
        )
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": forecast_line}})

    if alert.dashboard_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open dashboard"},
                        "url": alert.dashboard_url,
                    }
                ],
            }
        )

    return blocks


def build_payload(alert: BudgetAlert) -> dict[str, object]:
    """Full webhook body, including fallback text for notifications."""
    return {
        "text": (
            f"{alert.team} budget {alert.utilization:.0%} "
            f"({_money(alert.spend_to_date)} of {_money(alert.budget_limit)})"
        ),
        "blocks": build_blocks(alert),
    }


class SlackNotifier:
    """Posts alerts to a Slack incoming webhook."""

    def __init__(self, webhook_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._webhook_url = webhook_url
        self._client = client

    async def send(self, alert: BudgetAlert) -> bool:
        """Post the alert. A delivery failure is logged, never raised.

        An alerting path that can crash the evaluator would take budget
        monitoring down with it, which is the opposite of the point.
        """
        payload = build_payload(alert)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)

        try:
            response = await client.post(self._webhook_url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as error:
            log.warning(
                "slack_delivery_failed",
                team=alert.team,
                threshold=alert.threshold,
                error=str(error),
            )
            return False
        finally:
            if owns_client:
                await client.aclose()

        log.info("slack_alert_sent", team=alert.team, threshold=alert.threshold)
        return True
