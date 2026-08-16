"""Tests for shared-cost allocation strategies."""

from decimal import Decimal

import pytest

from finopsai.attribution.models import CostRecord, CostSource
from finopsai.attribution.rules import (
    STEP,
    AllocationRule,
    AllocationStrategy,
    even_weights,
    select_rule,
    split_amount,
)
from finopsai.attribution.tags import UNATTRIBUTED

THREE_TEAMS = ("search", "support-bot", "research")


def _record(**overrides: object) -> CostRecord:
    values: dict[str, object] = {
        "source": CostSource.VECTORDB,
        "dedup_key": "shared-1",
        "occurred_at": None,
        "amount_usd": Decimal("10.00"),
        "team": UNATTRIBUTED,
        "project": "shared",
        "agent_id": "vector-index",
        "use_case": "rag",
        "allocated": False,
        "allocation_parent_id": None,
    }
    values.update(overrides)
    return CostRecord(**values)  # type: ignore[arg-type]


def test_even_split_of_ten_across_three_sums_exactly() -> None:
    # Act
    shares = split_amount(Decimal("10.00"), even_weights(THREE_TEAMS), step=Decimal("0.01"))

    # Assert: the penny has to land somewhere, and nowhere else
    assert sum(shares.values()) == Decimal("10.00")
    assert sorted(shares.values()) == [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")]


@pytest.mark.parametrize(
    "amount",
    [
        pytest.param(Decimal("0.01"), id="one-cent"),
        pytest.param(Decimal("0.02"), id="two-cents"),
        pytest.param(Decimal("1.00"), id="one-dollar"),
        pytest.param(Decimal("10.00"), id="ten"),
        pytest.param(Decimal("99.99"), id="awkward"),
        pytest.param(Decimal("1234.56789012"), id="high-precision"),
        pytest.param(Decimal("0"), id="zero"),
    ],
)
def test_shares_always_sum_to_the_parent(amount: Decimal) -> None:
    # Act
    shares = split_amount(amount, even_weights(THREE_TEAMS))

    # Assert
    assert sum(shares.values()) == amount


@pytest.mark.parametrize("team_count", [1, 2, 3, 4, 5, 6, 7, 11, 13])
def test_shares_sum_exactly_for_any_team_count(team_count: int) -> None:
    # Arrange
    targets = tuple(f"team-{index}" for index in range(team_count))

    # Act
    shares = split_amount(Decimal("100.00"), even_weights(targets), step=Decimal("0.01"))

    # Assert
    assert sum(shares.values()) == Decimal("100.00")
    assert len(shares) == team_count


def test_remainder_goes_to_the_largest_fractional_share() -> None:
    # Arrange: weights 1/1/1 of 0.10 leaves a remainder of one cent
    shares = split_amount(Decimal("0.10"), even_weights(("a", "b", "c")), step=Decimal("0.01"))

    # Assert: exactly one team gets the extra cent, ties broken by name
    assert sum(shares.values()) == Decimal("0.10")
    assert sorted(shares.values()) == [Decimal("0.03"), Decimal("0.03"), Decimal("0.04")]
    assert shares["a"] == Decimal("0.04")


def test_splitting_is_deterministic() -> None:
    # Assert
    first = split_amount(Decimal("10.00"), even_weights(THREE_TEAMS))
    second = split_amount(Decimal("10.00"), even_weights(THREE_TEAMS))
    assert first == second


def test_weighted_split_respects_proportions() -> None:
    # Arrange
    weights = {"search": Decimal(60), "support-bot": Decimal(30), "research": Decimal(10)}

    # Act
    shares = split_amount(Decimal("100.00"), weights, step=Decimal("0.01"))

    # Assert
    assert shares == {
        "search": Decimal("60.00"),
        "support-bot": Decimal("30.00"),
        "research": Decimal("10.00"),
    }
    assert sum(shares.values()) == Decimal("100.00")


def test_zero_weight_team_receives_nothing_but_is_still_present() -> None:
    # Arrange
    weights = {"search": Decimal(70), "support-bot": Decimal(30), "research": Decimal(0)}

    # Act
    shares = split_amount(Decimal("100.00"), weights, step=Decimal("0.01"))

    # Assert
    assert shares["research"] == Decimal(0)
    assert sum(shares.values()) == Decimal("100.00")


def test_smallest_unit_cannot_be_divided_further() -> None:
    # Act: one step across three teams
    shares = split_amount(STEP, even_weights(THREE_TEAMS))

    # Assert: one team gets it, the rest get nothing, total is preserved
    assert sum(shares.values()) == STEP
    assert sorted(shares.values()) == [Decimal(0), Decimal(0), STEP]


def test_negative_amounts_are_rejected() -> None:
    # Credits and refunds need signed handling this does not implement yet.
    with pytest.raises(ValueError, match="credits"):
        split_amount(Decimal("-1.00"), even_weights(THREE_TEAMS))


@pytest.mark.parametrize(
    ("weights", "match"),
    [
        pytest.param({}, "without weights", id="empty"),
        pytest.param({"a": Decimal(0)}, "positive", id="all-zero"),
    ],
)
def test_invalid_weights_are_rejected(weights: dict[str, Decimal], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        split_amount(Decimal("1.00"), weights)


def test_fixed_percent_rule_requires_weights_for_every_target() -> None:
    with pytest.raises(ValueError, match="no weight for"):
        AllocationRule(
            name="bad",
            strategy=AllocationStrategy.FIXED_PERCENT,
            targets=("a", "b"),
            weights={"a": Decimal(1)},
        )


def test_fixed_percent_rule_requires_weights_at_all() -> None:
    with pytest.raises(ValueError, match="no weights"):
        AllocationRule(
            name="bad",
            strategy=AllocationStrategy.FIXED_PERCENT,
            targets=("a",),
        )


def test_rule_requires_targets() -> None:
    with pytest.raises(ValueError, match="target team"):
        AllocationRule(name="bad", strategy=AllocationStrategy.EVEN_SPLIT, targets=())


def test_lower_priority_number_wins() -> None:
    # Arrange
    specific = AllocationRule(
        name="vectordb-only",
        strategy=AllocationStrategy.EVEN_SPLIT,
        targets=THREE_TEAMS,
        source=CostSource.VECTORDB,
        priority=10,
    )
    catch_all = AllocationRule(
        name="everything",
        strategy=AllocationStrategy.EVEN_SPLIT,
        targets=THREE_TEAMS,
        priority=99,
    )

    # Act / Assert
    assert select_rule(_record(), [catch_all, specific]) is specific


def test_rule_ordering_is_independent_of_input_order() -> None:
    # Arrange
    first = AllocationRule(
        name="a-rule", strategy=AllocationStrategy.EVEN_SPLIT, targets=THREE_TEAMS, priority=5
    )
    second = AllocationRule(
        name="b-rule", strategy=AllocationStrategy.EVEN_SPLIT, targets=THREE_TEAMS, priority=5
    )

    # Assert: equal priority falls back to name, so the result is stable
    assert select_rule(_record(), [second, first]) is first


def test_non_matching_records_select_no_rule() -> None:
    # Arrange
    rule = AllocationRule(
        name="vectordb",
        strategy=AllocationStrategy.EVEN_SPLIT,
        targets=THREE_TEAMS,
        source=CostSource.VECTORDB,
    )

    # Assert: already-owned spend is not shared cost
    assert select_rule(_record(team="search"), [rule]) is None
    assert select_rule(_record(source=CostSource.LLM), [rule]) is None


def test_already_allocated_records_are_not_reprocessed() -> None:
    # Arrange
    rule = AllocationRule(
        name="vectordb", strategy=AllocationStrategy.EVEN_SPLIT, targets=THREE_TEAMS
    )

    # Assert
    assert select_rule(_record(allocated=True), [rule]) is None
    assert select_rule(_record(allocation_parent_id=1), [rule]) is None


def test_project_scoped_rule_only_matches_its_project() -> None:
    # Arrange
    rule = AllocationRule(
        name="shared-cluster",
        strategy=AllocationStrategy.EVEN_SPLIT,
        targets=THREE_TEAMS,
        match_project="shared",
    )

    # Assert
    assert select_rule(_record(project="shared"), [rule]) is rule
    assert select_rule(_record(project="discovery"), [rule]) is None
