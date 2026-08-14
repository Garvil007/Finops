"""Translate LiteLLM request tags and metadata into attribution dimensions.

The tag convention is a flat array of ``key:value`` strings, which is the only
shape LiteLLM records in ``LiteLLM_SpendLogs.request_tags``::

    ["team:search", "agent_id:demo", "use_case:rag", "project:demo"]

Spend that carries no usable tag is not discarded. It is attributed to
``unattributed``, because the share of spend nobody owns is a headline FinOps
metric rather than an error condition.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

UNATTRIBUTED = "unattributed"

TEAM = "team"
PROJECT = "project"
AGENT_ID = "agent_id"
USE_CASE = "use_case"

DIMENSION_KEYS: tuple[str, ...] = (TEAM, PROJECT, AGENT_ID, USE_CASE)

# LiteLLM stamps the virtual key's team alias onto every request it proxies, so
# a key issued per team attributes its spend even when the caller sends no tags.
TEAM_ALIAS_KEYS: tuple[str, ...] = ("user_api_key_team_alias", "team_alias")

TAG_SEPARATOR = ":"


@dataclass(frozen=True)
class Attribution:
    """The owner of a unit of spend."""

    team: str = UNATTRIBUTED
    project: str = UNATTRIBUTED
    agent_id: str = UNATTRIBUTED
    use_case: str = UNATTRIBUTED

    @property
    def is_attributed(self) -> bool:
        """True when at least the owning team is known."""
        return self.team != UNATTRIBUTED


def _normalize(value: object) -> str | None:
    """Return a trimmed lowercase string, or None when there is nothing usable."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def parse_tag_list(tags: Iterable[str] | None) -> dict[str, str]:
    """Parse ``key:value`` tags into a dimension mapping, ignoring unknown keys.

    The first occurrence of a key wins, so a duplicated tag cannot silently
    reattribute spend. Values may themselves contain colons.
    """
    parsed: dict[str, str] = {}
    for tag in tags or ():
        if not isinstance(tag, str) or TAG_SEPARATOR not in tag:
            continue
        raw_key, raw_value = tag.split(TAG_SEPARATOR, 1)
        key = _normalize(raw_key)
        value = _normalize(raw_value)
        if key in DIMENSION_KEYS and value is not None and key not in parsed:
            parsed[key] = value
    return parsed


def _from_metadata(metadata: Mapping[str, Any] | None, key: str) -> str | None:
    """Read a dimension straight off the metadata object."""
    if not metadata:
        return None
    return _normalize(metadata.get(key))


def _team_from_key_alias(metadata: Mapping[str, Any] | None) -> str | None:
    """Fall back to the virtual key's team alias."""
    if not metadata:
        return None
    for alias_key in TEAM_ALIAS_KEYS:
        alias = _normalize(metadata.get(alias_key))
        if alias is not None:
            return alias
    return None


def resolve_attribution(
    tags: Iterable[str] | None,
    metadata: Mapping[str, Any] | None = None,
) -> Attribution:
    """Resolve owners from tags first, then metadata, then the team alias."""
    parsed = parse_tag_list(tags)

    resolved = {
        key: parsed.get(key) or _from_metadata(metadata, key) or UNATTRIBUTED
        for key in DIMENSION_KEYS
    }

    if resolved[TEAM] == UNATTRIBUTED:
        resolved[TEAM] = _team_from_key_alias(metadata) or UNATTRIBUTED

    return Attribution(
        team=resolved[TEAM],
        project=resolved[PROJECT],
        agent_id=resolved[AGENT_ID],
        use_case=resolved[USE_CASE],
    )
