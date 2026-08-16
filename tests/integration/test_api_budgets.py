"""Integration tests for team and budget CRUD, and the forecast endpoint."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.attribution.forecast import month_bounds
from finopsai.attribution.models import CostRecord, CostSource

NOW = datetime.now(UTC)
MONTH_START, _ = month_bounds(NOW)


async def _seed_spend(sessions: async_sessionmaker[AsyncSession], team: str, amount: str) -> None:
    """Put spend inside the current month so the forecast can see it."""
    occurred = max(MONTH_START + timedelta(minutes=1), NOW - timedelta(hours=1))
    async with sessions() as session, session.begin():
        session.add(
            CostRecord(
                source=CostSource.LLM,
                dedup_key=f"spend-{team}",
                occurred_at=occurred,
                amount_usd=Decimal(amount),
                quantity=Decimal(10),
                unit="tokens",
                model="gpt-4o-mini",
                team=team,
                project="discovery",
                agent_id="agent",
                use_case="rag",
                raw={},
                allocated=False,
            )
        )


async def test_team_crud_round_trip(api_client: httpx.AsyncClient) -> None:
    # Create
    created = await api_client.post(
        "/api/v1/teams",
        json={"name": "search", "display_name": "Search Platform", "slack_channel": "#search"},
    )
    assert created.status_code == 201
    assert created.json()["display_name"] == "Search Platform"

    # Read
    listed = await api_client.get("/api/v1/teams")
    assert [team["name"] for team in listed.json()] == ["search"]
    assert (await api_client.get("/api/v1/teams/search")).status_code == 200

    # Delete
    assert (await api_client.delete("/api/v1/teams/search")).status_code == 204
    assert (await api_client.get("/api/v1/teams/search")).status_code == 404


async def test_duplicate_team_is_rejected(api_client: httpx.AsyncClient) -> None:
    # Arrange
    await api_client.post("/api/v1/teams", json={"name": "search"})

    # Act
    duplicate = await api_client.post("/api/v1/teams", json={"name": "search"})

    # Assert
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


async def test_missing_team_is_a_not_found(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/api/v1/teams/ghost")).status_code == 404
    assert (await api_client.delete("/api/v1/teams/ghost")).status_code == 404


async def test_budget_crud_round_trip(api_client: httpx.AsyncClient) -> None:
    # Create
    created = await api_client.post(
        "/api/v1/budgets",
        json={"team": "research", "period": "monthly", "limit_usd": 2000.0},
    )
    assert created.status_code == 201
    budget_id = created.json()["id"]
    assert created.json()["alert_thresholds"] == [80, 100]

    # Update
    patched = await api_client.patch(
        f"/api/v1/budgets/{budget_id}",
        json={"limit_usd": 3000.0, "alert_thresholds": [50, 90]},
    )
    assert patched.status_code == 200
    assert float(patched.json()["limit_usd"]) == 3000.0
    assert patched.json()["alert_thresholds"] == [50, 90]

    # Read
    listed = await api_client.get("/api/v1/budgets", params={"team": "research"})
    assert len(listed.json()) == 1

    # Delete
    assert (await api_client.delete(f"/api/v1/budgets/{budget_id}")).status_code == 204
    assert (await api_client.get(f"/api/v1/budgets/{budget_id}")).status_code == 404


async def test_partial_update_leaves_other_fields_alone(
    api_client: httpx.AsyncClient,
) -> None:
    # Arrange
    created = await api_client.post("/api/v1/budgets", json={"team": "search", "limit_usd": 500.0})
    budget_id = created.json()["id"]

    # Act
    patched = await api_client.patch(f"/api/v1/budgets/{budget_id}", json={"is_active": False})

    # Assert
    body = patched.json()
    assert body["is_active"] is False
    assert float(body["limit_usd"]) == 500.0


async def test_duplicate_budget_for_a_period_is_rejected(
    api_client: httpx.AsyncClient,
) -> None:
    # Arrange
    await api_client.post("/api/v1/budgets", json={"team": "research", "limit_usd": 100.0})

    # Act
    duplicate = await api_client.post(
        "/api/v1/budgets", json={"team": "research", "limit_usd": 200.0}
    )

    # Assert
    assert duplicate.status_code == 409


async def test_non_positive_budget_is_rejected(api_client: httpx.AsyncClient) -> None:
    # Act
    response = await api_client.post("/api/v1/budgets", json={"team": "research", "limit_usd": 0})

    # Assert
    assert response.status_code == 422


async def test_forecast_with_no_spend_projects_zero(api_client: httpx.AsyncClient) -> None:
    # Act
    response = await api_client.get("/api/v1/forecast", params={"team": "ghost"})

    # Assert
    body = response.json()
    assert response.status_code == 200
    assert float(body["spend_to_date"]) == 0.0
    assert float(body["projected_total"]) == 0.0
    assert body["will_breach"] is False
    assert body["method"] == "linear_run_rate"


async def test_forecast_projects_from_month_to_date_spend(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await _seed_spend(sessions, "research", "500.00")

    # Act
    response = await api_client.get("/api/v1/forecast", params={"team": "research"})

    # Assert
    body = response.json()
    assert float(body["spend_to_date"]) == 500.0
    assert float(body["projected_total"]) >= 500.0
    assert body["confidence"] in {"low", "medium", "high"}


async def test_forecast_reports_a_breach_against_an_active_budget(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange: spend already past a small budget
    await _seed_spend(sessions, "research", "500.00")
    await api_client.post("/api/v1/budgets", json={"team": "research", "limit_usd": 100.0})

    # Act
    response = await api_client.get("/api/v1/forecast", params={"team": "research"})

    # Assert
    body = response.json()
    assert body["will_breach"] is True
    assert body["breach_date"] is not None
    assert float(body["budget_limit"]) == 100.0
    assert body["projected_utilization"] > 1.0


async def test_forecast_ignores_an_inactive_budget(
    api_client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Arrange
    await _seed_spend(sessions, "research", "500.00")
    created = await api_client.post(
        "/api/v1/budgets", json={"team": "research", "limit_usd": 100.0}
    )
    await api_client.patch(f"/api/v1/budgets/{created.json()['id']}", json={"is_active": False})

    # Act
    response = await api_client.get("/api/v1/forecast", params={"team": "research"})

    # Assert
    assert response.json()["budget_limit"] is None
    assert response.json()["will_breach"] is False


async def test_forecast_requires_a_team(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/api/v1/forecast")).status_code == 422
