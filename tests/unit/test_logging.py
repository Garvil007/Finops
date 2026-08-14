"""Tests for structured logging configuration."""

import json

import pytest

from finopsai.config import Settings
from finopsai.logging import configure_logging, get_logger


def test_emits_json_lines(capsys: pytest.CaptureFixture[str], settings: Settings) -> None:
    # Arrange
    configure_logging(settings)
    logger = get_logger(__name__)

    # Act
    logger.info("collector_started", collector="litellm_spend")

    # Assert
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["event"] == "collector_started"
    assert payload["collector"] == "litellm_spend"
    assert payload["level"] == "info"
    assert "timestamp" in payload
