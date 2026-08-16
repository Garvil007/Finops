"""Tests for demo workload planning."""

import pytest

from finopsai.demo.workloads import (
    CHEAP_MODEL,
    EXPENSIVE_MODEL,
    MOCK_MODEL,
    TEAMS_BY_NAME,
    plan_calls,
)

SAMPLE = 4000
SEED = 1234


def test_planning_is_deterministic_for_a_seed() -> None:
    # Act
    first = plan_calls(200, SEED)
    second = plan_calls(200, SEED)

    # Assert
    assert first == second


def test_different_seeds_produce_different_plans() -> None:
    # Assert
    assert plan_calls(200, 1) != plan_calls(200, 2)


def test_untagged_share_is_close_to_the_configured_rate() -> None:
    # Act
    calls = plan_calls(SAMPLE, SEED)
    untagged = sum(1 for call in calls if not call.is_tagged)

    # Assert: 10% of calls carry no tags, within sampling tolerance
    assert 0.07 <= untagged / SAMPLE <= 0.13


def test_untagged_calls_render_no_tags() -> None:
    # Act
    calls = plan_calls(SAMPLE, SEED)
    untagged = [call for call in calls if not call.is_tagged]

    # Assert
    assert untagged
    assert all(call.tags() == [] for call in untagged)
    assert all(call.team is None for call in untagged)


def test_tagged_calls_render_all_four_dimensions() -> None:
    # Act
    call = next(call for call in plan_calls(SAMPLE, SEED) if call.is_tagged)
    keys = [tag.split(":", 1)[0] for tag in call.tags()]

    # Assert
    assert keys == ["team", "project", "agent_id", "use_case"]


def test_volume_is_weighted_support_bot_over_search_over_research() -> None:
    # Act
    calls = [call for call in plan_calls(SAMPLE, SEED) if call.is_tagged]
    counts = {name: 0 for name in TEAMS_BY_NAME}
    for call in calls:
        assert call.team is not None
        counts[call.team] += 1

    # Assert
    assert counts["support-bot"] > counts["search"] > counts["research"]


def test_agents_come_from_their_own_team() -> None:
    # Act
    calls = [call for call in plan_calls(SAMPLE, SEED) if call.is_tagged]

    # Assert
    for call in calls:
        assert call.team is not None
        assert call.agent_id in TEAMS_BY_NAME[call.team].agents
        assert call.use_case in TEAMS_BY_NAME[call.team].use_cases


def test_live_mode_skews_research_to_the_expensive_model() -> None:
    # Act
    calls = [call for call in plan_calls(SAMPLE, SEED, mock_mode=False) if call.team == "research"]
    expensive = sum(1 for call in calls if call.model == EXPENSIVE_MODEL)

    # Assert
    assert calls
    assert expensive / len(calls) > 0.7


def test_live_mode_skews_support_bot_to_the_cheap_model() -> None:
    # Act
    calls = [
        call for call in plan_calls(SAMPLE, SEED, mock_mode=False) if call.team == "support-bot"
    ]
    cheap = sum(1 for call in calls if call.model == CHEAP_MODEL)

    # Assert
    assert cheap / len(calls) > 0.85


def test_mock_mode_uses_only_the_offline_model() -> None:
    # Assert
    assert {call.model for call in plan_calls(500, SEED)} == {MOCK_MODEL}


def test_burst_makes_one_agent_dominate() -> None:
    # Act
    calls = plan_calls(SAMPLE, SEED, burst_agent="research/experiment-planner")
    bursting = sum(1 for call in calls if call.agent_id == "experiment-planner")

    # Assert: the runaway agent owns the majority of traffic
    assert bursting / SAMPLE > 0.5


def test_burst_defaults_to_the_first_agent_of_a_team() -> None:
    # Act
    calls = plan_calls(500, SEED, burst_agent="search")
    bursting = [call for call in calls if call.team == "search"]

    # Assert
    assert any(call.agent_id == "query-rewriter" for call in bursting)


@pytest.mark.parametrize(
    "burst_agent",
    [
        pytest.param("nope/agent", id="unknown-team"),
        pytest.param("search/not-an-agent", id="unknown-agent"),
    ],
)
def test_unknown_burst_targets_are_rejected(burst_agent: str) -> None:
    # Assert
    with pytest.raises(ValueError, match="unknown"):
        plan_calls(10, SEED, burst_agent=burst_agent)


def test_invalid_arguments_are_rejected() -> None:
    with pytest.raises(ValueError, match="count"):
        plan_calls(-1, SEED)
    with pytest.raises(ValueError, match="untagged_share"):
        plan_calls(10, SEED, untagged_share=1.5)
