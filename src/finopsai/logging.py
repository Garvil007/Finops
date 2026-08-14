"""Structured JSON logging configuration built on structlog."""

import logging
import sys
from typing import cast

import structlog
from structlog.typing import FilteringBoundLogger, Processor

from finopsai.config import Settings

DEFAULT_LEVEL = logging.INFO


def _resolve_level(name: str) -> int:
    """Translate a level name such as ``"INFO"`` into its numeric value."""
    return logging.getLevelNamesMapping().get(name.upper(), DEFAULT_LEVEL)


def configure_logging(settings: Settings) -> None:
    """Configure structlog to emit JSON lines on stdout at the configured level."""
    level = _resolve_level(settings.log_level)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Return a bound logger for the given module name."""
    return cast(FilteringBoundLogger, structlog.get_logger(name))
