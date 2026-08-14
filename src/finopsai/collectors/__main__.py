"""Entrypoint for the collectors service.

Runs every enabled collector and exposes a Prometheus scrape endpoint. Shuts
down cleanly on SIGTERM so `docker compose down` does not truncate a cycle
mid-transaction.
"""

import asyncio
import contextlib
import signal

from prometheus_client import start_http_server

from finopsai.collectors.runner import build_collectors, run_collectors
from finopsai.config import get_settings
from finopsai.logging import configure_logging, get_logger


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
    """Configure the process and run collectors until signalled."""
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)

    start_http_server(settings.metrics_port)
    log.info("metrics_server_started", port=settings.metrics_port)

    stop = asyncio.Event()
    _install_signal_handlers(stop)

    await run_collectors(build_collectors(settings), stop)


def main() -> None:
    """Console entrypoint."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
