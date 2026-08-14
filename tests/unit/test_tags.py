"""Tests for request-tag attribution parsing."""

import pytest

from finopsai.attribution.tags import (
    UNATTRIBUTED,
    Attribution,
    parse_tag_list,
    resolve_attribution,
)


def test_parses_the_documented_tag_convention() -> None:
    # Arrange
    tags = ["team:search", "agent_id:demo", "use_case:rag", "project:demo"]

    # Act
    attribution = resolve_attribution(tags)

    # Assert
    assert attribution == Attribution(
        team="search", project="demo", agent_id="demo", use_case="rag"
    )
    assert attribution.is_attributed


def test_values_may_contain_colons() -> None:
    # Act
    parsed = parse_tag_list(["use_case:rag:v2"])

    # Assert
    assert parsed["use_case"] == "rag:v2"


def test_first_occurrence_of_a_key_wins() -> None:
    # Act
    parsed = parse_tag_list(["team:search", "team:payments"])

    # Assert
    assert parsed["team"] == "search"


@pytest.mark.parametrize(
    "tags",
    [
        pytest.param(None, id="none"),
        pytest.param([], id="empty"),
        pytest.param(["malformed"], id="no-separator"),
        pytest.param(["team:"], id="empty-value"),
        pytest.param(["unknown:value"], id="unknown-key"),
        pytest.param([":orphan"], id="empty-key"),
    ],
)
def test_untagged_spend_is_unattributed_not_dropped(tags: list[str] | None) -> None:
    # Act
    attribution = resolve_attribution(tags)

    # Assert
    assert attribution.team == UNATTRIBUTED
    assert not attribution.is_attributed


def test_values_are_normalized() -> None:
    # Act
    attribution = resolve_attribution(["  TEAM : Search  "])

    # Assert
    assert attribution.team == "search"


def test_metadata_fills_dimensions_missing_from_tags() -> None:
    # Act
    attribution = resolve_attribution(["team:search"], {"use_case": "RAG"})

    # Assert
    assert attribution.team == "search"
    assert attribution.use_case == "rag"


def test_tags_win_over_metadata() -> None:
    # Act
    attribution = resolve_attribution(["team:search"], {"team": "payments"})

    # Assert
    assert attribution.team == "search"


def test_virtual_key_team_alias_is_the_last_resort() -> None:
    # Act
    attribution = resolve_attribution(None, {"user_api_key_team_alias": "Platform"})

    # Assert
    assert attribution.team == "platform"
    assert attribution.project == UNATTRIBUTED
