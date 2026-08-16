"""Integration tests for the cost query API."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.attribution.models import CostRecord, CostSource
from finopsai.attribution.tags import UNATTRIBUTED

NOW = datetime.now(UTC)


def _record(
    key: str,
    amount: str,
    team: str = "search",
    source: CostSource = CostSource.LLM,
    days_ago: float = 1.0,
    model: str | None = "gpt-4o-mini",
    project: str = "discovery",
) -> CostRecord:
    return CostRecord(
        source=source,
        dedup_key=key,
        occurred_at=NOW - timedelta(days=days_ago),
        amount_usd=Decimal(amount),
        quantity=Decimal(10),
        unit="tokens",
        model=model,
        team=team,
        project=project,
        agent_id="agent",
        use_case="rag",
        raw={},
        allocated=False,
    )


def _record_at(key: str, amount: str, occurred_at: datetime) -> CostRecord:
    """A record pinned to an exact instant, for bucket-boundary tests."""
    record = _record(key, amount)
    record.occurred_at = occurred_at
    return record


async def _seed(sessions: async_sessionmaker[AsyncSession], *records: CostRecord) -> None:
    async with sessions() as session, session.begin():
        session.add_all(records)


async def test_healthz_never_touches_the_database(api_client: httpx.AsyncClient) -> None:
    # Act
    response = await api_client.get("/healthz")

    # Assert
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readyz_checks_the_database(api_client: httpx.AsyncClient) -> None:
    # Act
    response = await api_client.get("/readyz")

    # Assert
    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"


async def test_costs_group_by_team_with_source_subtotals(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("a", "10.00", team="search", source=CostSource.LLM),
        _record("b", "5.00", team="search", source=CostSource.COMPUTE),
        _record("c", "20.00", team="research"),
    )

    # Act
    response = await api_client.get("/api/v1/costs", params={"group_by": "team", "period": "30d"})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert float(body["total_usd"]) == 35.0
    assert [group["key"]["team"] for group in body["groups"]] == ["research", "search"]

    search = next(g for g in body["groups"] if g["key"]["team"] == "search")
    assert float(search["by_source"]["llm"]) == 10.0
    assert float(search["by_source"]["compute"]) == 5.0
    assert search["record_count"] == 2


async def test_costs_compare_against_the_previous_window(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange: 150 this week, 100 the week before
    await _seed(
        sessions,
        _record("now", "150.00", days_ago=1),
        _record("before", "100.00", days_ago=10),
    )

    # Act
    response = await api_client.get("/api/v1/costs", params={"period": "7d"})

    # Assert
    body = response.json()
    assert float(body["total_usd"]) == 150.0
    assert float(body["previous_total_usd"]) == 100.0
    assert body["change_percent"] == 50.0
    assert body["groups"][0]["change_percent"] == 50.0


async def test_change_percent_is_null_without_a_baseline(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await _seed(sessions, _record("only", "10.00", days_ago=1))

    # Act
    response = await api_client.get("/api/v1/costs", params={"period": "7d"})

    # Assert
    assert response.json()["change_percent"] is None


async def test_costs_filter_by_team(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("a", "10.00", team="search"),
        _record("b", "90.00", team="research"),
    )

    # Act
    response = await api_client.get("/api/v1/costs", params={"team": "search"})

    # Assert
    body = response.json()
    assert len(body["groups"]) == 1
    assert float(body["total_usd"]) == 10.0


async def test_multi_dimension_grouping(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("a", "10.00", team="search", project="discovery"),
        _record("b", "20.00", team="search", project="model-lab"),
    )

    # Act
    response = await api_client.get("/api/v1/costs", params={"group_by": "team,project"})

    # Assert
    keys = {(g["key"]["team"], g["key"]["project"]) for g in response.json()["groups"]}
    assert keys == {("search", "discovery"), ("search", "model-lab")}


async def test_unknown_dimension_is_a_client_error(api_client: httpx.AsyncClient) -> None:
    # Act
    response = await api_client.get("/api/v1/costs", params={"group_by": "team; drop table x"})

    # Assert
    assert response.status_code == 400
    assert "unknown dimension" in response.json()["detail"]


async def test_invalid_period_is_a_client_error(api_client: httpx.AsyncClient) -> None:
    # Act
    response = await api_client.get("/api/v1/costs", params={"period": "forever"})

    # Assert
    assert response.status_code == 400
    assert "invalid period" in response.json()["detail"]


async def test_unattributed_report(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await _seed(
        sessions,
        _record("owned", "60.00", team="search"),
        _record("orphan", "40.00", team=UNATTRIBUTED, model="gpt-4o-mini"),
    )

    # Act
    response = await api_client.get("/api/v1/costs/unattributed", params={"period": "30d"})

    # Assert
    body = response.json()
    assert float(body["unattributed_usd"]) == 40.0
    assert float(body["attributed_usd"]) == 60.0
    assert body["unattributed_share"] == 0.4
    assert body["top_models"][0]["label"] == "gpt-4o-mini"


async def test_unattributed_report_on_an_empty_warehouse(
    api_client: httpx.AsyncClient,
) -> None:
    # Act
    response = await api_client.get("/api/v1/costs/unattributed")

    # Assert: no division by zero
    assert response.status_code == 200
    assert response.json()["unattributed_share"] == 0.0


async def test_timeseries_buckets_by_day(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange: two records inside one UTC day, one on the day after. Anchored to
    # an explicit hour because "2.2 days ago" lands on a different calendar day
    # depending on the time of the run.
    day = (NOW - timedelta(days=3)).replace(hour=6, minute=0, second=0, microsecond=0)
    records = [
        _record_at("morning", "10.00", day),
        _record_at("afternoon", "20.00", day + timedelta(hours=8)),
        _record_at("next-day", "5.00", day + timedelta(days=1)),
    ]
    await _seed(sessions, *records)

    # Act
    response = await api_client.get(
        "/api/v1/costs/timeseries", params={"group_by": "team", "interval": "1d", "period": "7d"}
    )

    # Assert: the two same-day records collapse into one bucket, oldest first
    body = response.json()
    assert body["interval"] == "1d"
    assert len(body["points"]) == 2
    buckets = [point["bucket"] for point in body["points"]]
    assert buckets == sorted(buckets)
    assert float(body["points"][0]["amount_usd"]) == 30.0
    assert float(body["points"][1]["amount_usd"]) == 5.0


async def test_timeseries_buckets_by_hour(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    hour = (NOW - timedelta(hours=5)).replace(minute=0, second=0, microsecond=0)
    await _seed(
        sessions,
        _record_at("h1-a", "1.00", hour + timedelta(minutes=5)),
        _record_at("h1-b", "2.00", hour + timedelta(minutes=45)),
        _record_at("h2", "4.00", hour + timedelta(hours=1)),
    )

    # Act
    response = await api_client.get(
        "/api/v1/costs/timeseries", params={"interval": "1h", "period": "24h"}
    )

    # Assert
    points = response.json()["points"]
    assert len(points) == 2
    assert float(points[0]["amount_usd"]) == 3.0


async def test_timeseries_rejects_a_bad_interval(api_client: httpx.AsyncClient) -> None:
    # Act
    response = await api_client.get("/api/v1/costs/timeseries", params={"interval": "1y"})

    # Assert: an unsupported enum value is caught by validation
    assert response.status_code == 422


async def test_openapi_documents_the_endpoints(api_client: httpx.AsyncClient) -> None:
    # Act
    response = await api_client.get("/openapi.json")

    # Assert: the docs page is part of the deliverable
    schema = response.json()
    assert schema["info"]["title"] == "FinOpsAI"
    assert "/api/v1/costs" in schema["paths"]
    assert "/api/v1/forecast" in schema["paths"]
    assert "/api/v1/budgets" in schema["paths"]
    example = schema["components"]["schemas"]["CostGroupModel"]["example"]
    assert example["key"] == {"team": "research"}


async def test_metrics_endpoint_exposes_request_counters(
    api_client: httpx.AsyncClient,
) -> None:
    # Arrange
    await api_client.get("/healthz")

    # Act
    response = await api_client.get("/metrics")

    # Assert
    assert response.status_code == 200
    assert "finopsai_http_requests_total" in response.text


async def test_request_id_is_echoed(api_client: httpx.AsyncClient) -> None:
    # Act
    response = await api_client.get("/healthz", headers={"x-request-id": "abc-123"})

    # Assert
    assert response.headers["x-request-id"] == "abc-123"
