"""Entrypoint for the background worker service.

Runs the collectors *and* the budget evaluator in one process.

The evaluator lives here rather than in its own container on purpose. It is a
single query loop that runs every fifteen minutes against the same warehouse the
collectors already hold a pool to, and it has no independent scaling or failure
story -- a second image, service definition, and set of credentials would buy
nothing but more to operate. It also wants to run *after* collection rather than
against a stale warehouse, which co-location makes trivial. If evaluation ever
grows expensive enough to compete with collection for the pool, splitting it out
is a compose change and a different entrypoint, not a rewrite.

Exposes a Prometheus scrape endpoint and shuts down cleanly on SIGTERM.
"""

import asyncio
import contextlib
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from prometheus_client import start_http_server
from redis.asyncio import Redis

from finopsai.alerting.alerts import Notifier, NullNotifier
from finopsai.alerting.evaluator import BudgetEvaluator
from finopsai.alerting.slack import SlackNotifier
from finopsai.alerting.state import AlertStateStore, RedisAlertState
from finopsai.collectors.runner import build_collectors, run_collectors
from finopsai.config import Settings, get_settings
from finopsai.db import create_session_factory, create_warehouse_engine
from finopsai.logging import configure_logging, get_logger

log = get_logger(__name__)


def build_notifier(settings: Settings) -> Notifier:
    """Slack when a webhook is configured, a logging no-op otherwise."""
    if settings.slack_webhook_url is None:
        log.warning("slack_webhook_not_configured_alerts_will_only_be_logged")
        return NullNotifier()
    return SlackNotifier(settings.slack_webhook_url.get_secret_value())


def build_alert_state(settings: Settings) -> AlertStateStore:
    """Redis-backed alert state, shared across restarts."""
    return RedisAlertState(Redis.from_url(settings.redis_url))


def build_evaluator(settings: Settings) -> BudgetEvaluator:
    """Assemble the budget evaluator from settings."""
    sessions = create_session_factory(create_warehouse_engine(settings))
    return BudgetEvaluator(
        sessions=sessions,
        state=build_alert_state(settings),
        notifier=build_notifier(settings),
        dashboard_url=settings.grafana_dashboard_url,
    )


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Ask the loop to set the stop event on termination signals."""
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signal_name, None)
        if sig is None:
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)


async def async_main() -> None:
    """Run collectors and the alert evaluator until signalled."""
    settings = get_settings()
    configure_logging(settings)

    start_http_server(settings.metrics_port)
    log.info("metrics_server_started", port=settings.metrics_port)

    evaluator = build_evaluator(settings)
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        evaluator.run_once,
        trigger="interval",
        minutes=settings.alert_interval_minutes,
        id="budget_evaluation",
        # A slow cycle must not stack up behind itself; skip instead.
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("alert_scheduler_started", interval_minutes=settings.alert_interval_minutes)

    stop = asyncio.Event()
    _install_signal_handlers(stop)

    try:
        await run_collectors(build_collectors(settings), stop)
    finally:
        scheduler.shutdown(wait=False)
        log.info("alert_scheduler_stopped")


def main() -> None:
    """Console entrypoint."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
