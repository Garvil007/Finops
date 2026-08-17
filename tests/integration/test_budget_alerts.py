"""Tests for budget threshold evaluation and alert delivery."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finopsai.alerting.alerts import BudgetAlert, CollectingNotifier, NullNotifier
from finopsai.alerting.evaluator import crossed_threshold, evaluate_budgets
from finopsai.alerting.slack import build_payload, utilization_bar
from finopsai.alerting.state import InMemoryAlertState, period_key
from finopsai.attribution.models import Budget, BudgetPeriod, CostRecord, CostSource

AUGUST = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


async def _seed_budget(
    sessions: async_sessionmaker[AsyncSession],
    team: str = "research",
    limit: str = "1000.00",
    thresholds: list[int] | None = None,
    is_active: bool = True,
    period: BudgetPeriod = BudgetPeriod.MONTHLY,
) -> int:
    async with sessions() as session, session.begin():
        budget = Budget(
            team=team,
            period=period,
            limit_usd=Decimal(limit),
            alert_thresholds=thresholds if thresholds is not None else [80, 100],
            is_active=is_active,
        )
        session.add(budget)
        await session.flush()
        return int(budget.id)


async def _seed_spend(
    sessions: async_sessionmaker[AsyncSession],
    amount: str,
    team: str = "research",
    when: datetime | None = None,
    key: str = "spend-1",
    model: str | None = "claude-haiku-4-5",
) -> None:
    occurred = when or AUGUST - timedelta(days=1)
    async with sessions() as session, session.begin():
        session.add(
            CostRecord(
                source=CostSource.LLM,
                dedup_key=key,
                occurred_at=occurred,
                amount_usd=Decimal(amount),
                quantity=Decimal(100),
                unit="tokens",
                model=model,
                team=team,
                project="model-lab",
                agent_id="agent",
                use_case="experiment-design",
                raw={},
                allocated=False,
            )
        )


@pytest.mark.parametrize(
    ("utilization", "expected"),
    [
        pytest.param(0.0, None, id="no-spend"),
        pytest.param(0.799, None, id="just-below"),
        pytest.param(0.80, 80, id="exactly-at-boundary"),
        pytest.param(0.95, 80, id="between"),
        pytest.param(1.0, 100, id="exactly-exhausted"),
        pytest.param(1.5, 100, id="overspent"),
    ],
)
def test_threshold_crossing(utilization: float, expected: int | None) -> None:
    # Assert: the boundary counts as crossed, not as not-yet
    assert crossed_threshold(utilization, [80, 100]) == expected


def test_period_key_changes_with_the_month() -> None:
    # Assert: rollover is inherent in the key, not a job that can be missed
    assert period_key(AUGUST) == "2026-08"
    assert period_key(SEPTEMBER) == "2026-09"


async def test_alert_fires_when_a_threshold_is_crossed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange: 850 of a 1000 budget is past 80%
    await _seed_budget(sessions)
    await _seed_spend(sessions, "850.00")
    notifier = CollectingNotifier()

    # Act
    async with sessions() as session:
        alerts = await evaluate_budgets(session, InMemoryAlertState(), notifier, now=AUGUST)

    # Assert
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.threshold == 80
    assert alert.team == "research"
    assert alert.spend_to_date == Decimal("850.00")
    assert len(notifier.sent) == 1


async def test_no_alert_below_the_first_threshold(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed_budget(sessions)
    await _seed_spend(sessions, "100.00")
    notifier = CollectingNotifier()

    # Act
    async with sessions() as session:
        alerts = await evaluate_budgets(session, InMemoryAlertState(), notifier, now=AUGUST)

    # Assert
    assert alerts == []
    assert notifier.sent == []


async def test_re_evaluation_does_not_fire_twice(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed_budget(sessions)
    await _seed_spend(sessions, "850.00")
    state = InMemoryAlertState()
    notifier = CollectingNotifier()

    # Act: the scheduler runs every fifteen minutes; nothing changed
    async with sessions() as session:
        first = await evaluate_budgets(session, state, notifier, now=AUGUST)
        second = await evaluate_budgets(
            session, state, notifier, now=AUGUST + timedelta(minutes=15)
        )
        third = await evaluate_budgets(session, state, notifier, now=AUGUST + timedelta(hours=6))

    # Assert
    assert len(first) == 1
    assert second == []
    assert third == []
    assert len(notifier.sent) == 1


async def test_crossing_a_higher_threshold_escalates(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed_budget(sessions)
    await _seed_spend(sessions, "850.00", key="spend-1")
    state = InMemoryAlertState()
    notifier = CollectingNotifier()

    async with sessions() as session:
        await evaluate_budgets(session, state, notifier, now=AUGUST)

    # Act: spend grows past the budget
    await _seed_spend(sessions, "300.00", key="spend-2")
    async with sessions() as session:
        escalated = await evaluate_budgets(
            session, state, notifier, now=AUGUST + timedelta(hours=1)
        )

    # Assert: 100% is news even though 80% was already announced
    assert len(escalated) == 1
    assert escalated[0].threshold == 100
    assert escalated[0].is_exhausted is True
    assert [alert.threshold for alert in notifier.sent] == [80, 100]


async def test_month_rollover_resets_the_state(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed_budget(sessions)
    await _seed_spend(sessions, "850.00", key="aug", when=AUGUST - timedelta(days=1))
    state = InMemoryAlertState()
    notifier = CollectingNotifier()

    async with sessions() as session:
        await evaluate_budgets(session, state, notifier, now=AUGUST)

    # Act: a new month, with its own spend
    await _seed_spend(sessions, "900.00", key="sep", when=SEPTEMBER - timedelta(days=1))
    async with sessions() as session:
        september = await evaluate_budgets(session, state, notifier, now=SEPTEMBER)

    # Assert: the new period announces from scratch
    assert len(september) == 1
    assert september[0].threshold == 80
    assert september[0].period_key == "2026-09"


async def test_previous_month_spend_is_not_counted(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange: all the spend is in July
    await _seed_budget(sessions)
    await _seed_spend(sessions, "5000.00", when=datetime(2026, 7, 20, tzinfo=UTC))
    notifier = CollectingNotifier()

    # Act
    async with sessions() as session:
        alerts = await evaluate_budgets(session, InMemoryAlertState(), notifier, now=AUGUST)

    # Assert: a budget is period-to-date, not lifetime
    assert alerts == []


async def test_inactive_budgets_are_skipped(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed_budget(sessions, is_active=False)
    await _seed_spend(sessions, "5000.00")
    notifier = CollectingNotifier()

    # Act
    async with sessions() as session:
        alerts = await evaluate_budgets(session, InMemoryAlertState(), notifier, now=AUGUST)

    # Assert
    assert alerts == []


async def test_alert_carries_forecast_and_drivers(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange
    await _seed_budget(sessions)
    await _seed_spend(sessions, "600.00", key="haiku", model="claude-haiku-4-5")
    await _seed_spend(sessions, "300.00", key="mini", model="gpt-4o-mini")
    notifier = CollectingNotifier()

    # Act
    async with sessions() as session:
        alerts = await evaluate_budgets(session, InMemoryAlertState(), notifier, now=AUGUST)

    # Assert
    alert = alerts[0]
    assert alert.projected_total > alert.spend_to_date
    assert alert.breach_date is not None
    assert [driver.label for driver in alert.drivers][:2] == [
        "claude-haiku-4-5",
        "gpt-4o-mini",
    ]


async def test_a_failing_budget_does_not_stop_the_others(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Arrange: a zero limit would divide by zero if it were not guarded
    await _seed_budget(sessions, team="broken", limit="0.00000001")
    await _seed_budget(sessions, team="research")
    await _seed_spend(sessions, "850.00", team="research")
    notifier = CollectingNotifier()

    # Act
    async with sessions() as session:
        alerts = await evaluate_budgets(session, InMemoryAlertState(), notifier, now=AUGUST)

    # Assert
    assert "research" in {alert.team for alert in alerts}


async def test_null_notifier_reports_that_nothing_was_sent() -> None:
    # Arrange
    alert = BudgetAlert(
        team="research",
        budget_id=1,
        threshold=80,
        period_key="2026-08",
        period_start=AUGUST,
        period_end=SEPTEMBER,
        spend_to_date=Decimal("800.00"),
        budget_limit=Decimal("1000.00"),
        utilization=0.8,
        projected_total=Decimal("1600.00"),
    )

    # Act / Assert
    assert await NullNotifier().send(alert) is False


def test_slack_payload_shape() -> None:
    # Arrange
    alert = BudgetAlert(
        team="research",
        budget_id=1,
        threshold=100,
        period_key="2026-08",
        period_start=AUGUST,
        period_end=SEPTEMBER,
        spend_to_date=Decimal("1200.00"),
        budget_limit=Decimal("1000.00"),
        utilization=1.2,
        projected_total=Decimal("2400.00"),
        breach_date=AUGUST,
        dashboard_url="http://grafana.local/d/finops",
    )

    # Act
    payload = build_payload(alert)
    kinds = [block["type"] for block in payload["blocks"]]

    # Assert: fallback text exists for notifications, and the button is present
    assert "research" in str(payload["text"])
    assert kinds[0] == "header"
    assert "actions" in kinds


def test_utilization_bar_clamps_at_full() -> None:
    # Assert: an overspent budget must not render a bar wider than the bar
    assert len(utilization_bar(2.5)) == len(utilization_bar(0.5))
    assert utilization_bar(0.0).count("\u2588") == 0
    assert utilization_bar(1.0).count("\u2591") == 0
