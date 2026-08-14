"""Supervises every enabled collector in one process.

Each collector owns its own interval and its own failure domain: a collector
that keeps erroring logs and retries without affecting the others.
"""

import asyncio

from finopsai.collectors.base import BaseCollector
from finopsai.config import Settings
from finopsai.db import (
    create_litellm_engine,
    create_session_factory,
    create_warehouse_engine,
)
from finopsai.logging import get_logger

log = get_logger(__name__)


def build_collectors(settings: Settings) -> list[BaseCollector]:
    """Construct the enabled collectors and the engines they read from."""
    from finopsai.collectors.litellm_spend import LiteLLMSpendCollector

    sessions = create_session_factory(create_warehouse_engine(settings))
    litellm_engine = create_litellm_engine(settings)

    return [LiteLLMSpendCollector(sessions, litellm_engine)]


async def run_collectors(
    collectors: list[BaseCollector], stop: asyncio.Event | None = None
) -> None:
    """Run every collector concurrently until stopped or cancelled."""
    log.info("collector_runner_started", collectors=[c.name for c in collectors])
    async with asyncio.TaskGroup() as group:
        for collector in collectors:
            group.create_task(collector.run_forever(stop), name=collector.name)
    log.info("collector_runner_stopped")
