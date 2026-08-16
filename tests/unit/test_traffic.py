"""Tests for the demo traffic generator, driven through a stub transport."""

from decimal import Decimal

import httpx
import pytest

from finopsai.demo.traffic import (
    COST_HEADER,
    TrafficOptions,
    build_request_body,
    response_cost,
    run_traffic,
)
from finopsai.demo.workloads import PlannedCall

COMPLETION_BODY = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


def _options(**overrides: object) -> TrafficOptions:
    defaults: dict[str, object] = {
        "base_url": "http://proxy.invalid",
        "master_key": "sk-test-master",
        "count": 40,
        "rate": 0.0,
        "seed": 7,
    }
    defaults.update(overrides)
    return TrafficOptions(**defaults)  # type: ignore[arg-type]


def _client(handler: object, cost: str | None = "0.0001") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        base_url="http://proxy.invalid",
    )


def test_tagged_calls_carry_metadata_tags() -> None:
    # Arrange
    call = PlannedCall(
        model="mock-gpt",
        prompt="hi",
        team="search",
        project="discovery",
        agent_id="reranker",
        use_case="rag",
    )

    # Act
    body = build_request_body(call, max_tokens=32)

    # Assert
    assert body["metadata"] == {
        "tags": ["team:search", "project:discovery", "agent_id:reranker", "use_case:rag"]
    }


def test_untagged_calls_omit_metadata_entirely() -> None:
    # Act
    body = build_request_body(PlannedCall(model="mock-gpt", prompt="hi"), max_tokens=32)

    # Assert: an empty tag list would still create an attributable row
    assert "metadata" not in body


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        pytest.param("0.0025", Decimal("0.0025"), id="reported"),
        pytest.param(None, Decimal(0), id="absent"),
        pytest.param("not-a-number", Decimal(0), id="unparseable"),
    ],
)
def test_response_cost_reads_the_litellm_header(header: str | None, expected: Decimal) -> None:
    # Arrange
    headers = {} if header is None else {COST_HEADER: header}

    # Act / Assert
    assert response_cost(httpx.Response(200, headers=headers)) == expected


async def test_run_traffic_sends_every_planned_call() -> None:
    # Arrange
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-team"})
        return httpx.Response(200, json=COMPLETION_BODY, headers={COST_HEADER: "0.0001"})

    options = _options(count=25)

    # Act
    async with _client(handler) as client:
        report = await run_traffic(options, client)

    # Assert
    assert report.sent == 25
    assert report.failed == 0
    assert report.untagged > 0
    assert report.spend_usd == Decimal("0.0025")
    assert report.aborted_reason is None


async def test_run_traffic_aborts_at_the_spend_ceiling() -> None:
    # Arrange: each call reports a cent, so the ceiling trips early
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-team"})
        return httpx.Response(200, json=COMPLETION_BODY, headers={COST_HEADER: "0.01"})

    options = _options(count=500, max_spend_usd=Decimal("0.05"))

    # Act
    async with _client(handler) as client:
        report = await run_traffic(options, client)

    # Assert
    assert report.aborted_reason == "max_spend_reached"
    assert report.sent < 500


async def test_failed_calls_are_counted_not_raised() -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-team"})
        return httpx.Response(500, json={"error": "boom"})

    options = _options(count=5)

    # Act
    async with _client(handler) as client:
        report = await run_traffic(options, client)

    # Assert
    assert report.sent == 0
    assert report.failed == 5


async def test_master_key_is_used_when_team_keys_cannot_be_minted() -> None:
    # Arrange
    used: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            return httpx.Response(400, json={"error": "duplicate alias"})
        used.append(request.headers["authorization"])
        return httpx.Response(200, json=COMPLETION_BODY)

    options = _options(count=5)

    # Act
    async with _client(handler) as client:
        report = await run_traffic(options, client)

    # Assert
    assert report.sent == 5
    assert set(used) == {"Bearer sk-test-master"}
