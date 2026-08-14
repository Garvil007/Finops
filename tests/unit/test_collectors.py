"""Tests for collector plumbing: source coercion and the run loop."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.attribution.models import CostRecord, CostSource
from finopsai.collectors.base import BaseCollector
from finopsai.collectors.litellm_spend import _as_mapping, _as_tag_list, _as_utc


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param({"team": "search"}, {"team": "search"}, id="mapping"),
        pytest.param('{"team": "search"}', {"team": "search"}, id="json-string"),
        pytest.param("not json", None, id="invalid-json"),
        pytest.param("[1, 2]", None, id="json-but-not-object"),
        pytest.param(None, None, id="null"),
    ],
)
def test_metadata_column_is_coerced(value: Any, expected: dict[str, Any] | None) -> None:
    assert _as_mapping(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(["team:search"], ["team:search"], id="list"),
        pytest.param('["team:search"]', ["team:search"], id="json-string"),
        pytest.param(["team:search", 7], ["team:search"], id="drops-non-strings"),
        pytest.param("not json", [], id="invalid-json"),
        pytest.param(None, [], id="null"),
    ],
)
def test_request_tags_column_is_coerced(value: Any, expected: list[str]) -> None:
    assert _as_tag_list(value) == expected


def test_naive_timestamps_are_treated_as_utc() -> None:
    # Act
    resolved = _as_utc(datetime(2026, 8, 14, 12, 0))

    # Assert
    assert resolved == datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


class _StubCollector(BaseCollector):
    """Counts cycles so the loop can be observed."""

    name = "stub"
    source = CostSource.COMPUTE
    interval_seconds = 0.01

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(sessions)
        self.cycles = 0

    async def collect(self) -> list[CostRecord]:
        self.cycles += 1
        return []


async def test_run_forever_exits_when_stopped(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    collector = _StubCollector(sessions)
    stop = asyncio.Event()

    # Act
    task = asyncio.create_task(collector.run_forever(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    # Assert
    assert collector.cycles >= 1


async def test_empty_cycle_persists_nothing(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Act
    written = await _StubCollector(sessions).run_once()

    # Assert
    assert written == 0
