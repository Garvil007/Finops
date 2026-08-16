"""Workload profiles for the demo traffic generator.

Three teams with deliberately different cost shapes, so the dashboard shows a
spread rather than a flat line:

* ``support-bot`` -- high call volume on a cheap model. Lots of small spend.
* ``research`` -- low volume on an expensive model with long prompts. Few calls,
  large spend, and the team that also carries GPU cost in the compute collector.
* ``search`` -- mixed models and mid volume, the realistic middle case.

A configurable share of calls carries no tags at all. That is the point, not an
oversight: unattributable spend is the pain FinOps exists to expose, so the demo
has to contain some.

Planning is a pure function of a seed. Nothing here performs I/O, so the
distribution can be asserted in tests without a proxy.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

UNTAGGED_SHARE = 0.10
BURST_SHARE = 0.6

CHEAP_MODEL = "gpt-4o-mini"
EXPENSIVE_MODEL = "claude-haiku-4-5"
MOCK_MODEL = "mock-gpt"


@dataclass(frozen=True)
class TeamProfile:
    """How one team's LLM usage behaves."""

    name: str
    project: str
    agents: tuple[str, ...]
    use_cases: tuple[str, ...]
    model_weights: Mapping[str, float]
    volume_weight: float
    prompt_tokens: tuple[int, int]


TEAMS: tuple[TeamProfile, ...] = (
    TeamProfile(
        name="support-bot",
        project="customer-support",
        agents=("triage", "faq-responder", "escalation", "summarizer"),
        use_cases=("ticket-triage", "faq", "summarization"),
        model_weights={CHEAP_MODEL: 0.95, EXPENSIVE_MODEL: 0.05},
        volume_weight=0.60,
        prompt_tokens=(80, 260),
    ),
    TeamProfile(
        name="search",
        project="discovery",
        agents=("query-rewriter", "reranker", "answer-synth"),
        use_cases=("rag", "query-expansion", "reranking"),
        model_weights={CHEAP_MODEL: 0.65, EXPENSIVE_MODEL: 0.35},
        volume_weight=0.30,
        prompt_tokens=(150, 600),
    ),
    TeamProfile(
        name="research",
        project="model-lab",
        agents=("paper-reader", "experiment-planner"),
        use_cases=("literature-review", "experiment-design"),
        model_weights={CHEAP_MODEL: 0.15, EXPENSIVE_MODEL: 0.85},
        volume_weight=0.10,
        prompt_tokens=(900, 2400),
    ),
)

TEAMS_BY_NAME: Mapping[str, TeamProfile] = {team.name: team for team in TEAMS}

PROMPT_TEMPLATES: tuple[str, ...] = (
    "Summarize the following support ticket in one sentence.",
    "Rewrite this search query to improve recall.",
    "Extract the key claim from this paragraph.",
    "Draft a two-line reply to this customer message.",
    "List the assumptions this experiment depends on.",
)


@dataclass(frozen=True)
class PlannedCall:
    """One LLM call the generator intends to make."""

    model: str
    prompt: str
    team: str | None = None
    project: str | None = None
    agent_id: str | None = None
    use_case: str | None = None

    @property
    def is_tagged(self) -> bool:
        """False for the deliberately unattributed share."""
        return self.team is not None

    def tags(self) -> list[str]:
        """Render the ``key:value`` tag list LiteLLM stores in request_tags."""
        if not self.is_tagged:
            return []
        return [
            f"team:{self.team}",
            f"project:{self.project}",
            f"agent_id:{self.agent_id}",
            f"use_case:{self.use_case}",
        ]


def _weighted_choice(rng: random.Random, weights: Mapping[str, float]) -> str:
    """Pick a key in proportion to its weight."""
    keys = list(weights)
    return rng.choices(keys, weights=[weights[key] for key in keys], k=1)[0]


def _pick_team(rng: random.Random, teams: Sequence[TeamProfile]) -> TeamProfile:
    """Pick a team in proportion to its call volume."""
    return rng.choices(list(teams), weights=[team.volume_weight for team in teams], k=1)[0]


def plan_calls(
    count: int,
    seed: int,
    *,
    untagged_share: float = UNTAGGED_SHARE,
    mock_mode: bool = True,
    burst_agent: str | None = None,
    burst_share: float = BURST_SHARE,
) -> list[PlannedCall]:
    """Plan ``count`` calls deterministically from ``seed``.

    ``burst_agent`` simulates a runaway agent by giving one agent the majority
    of traffic, which is what the budget-alerting demo needs to trip a
    threshold. It is written as ``team/agent``.
    """
    if count < 0:
        raise ValueError("count must not be negative")
    if not 0.0 <= untagged_share <= 1.0:
        raise ValueError("untagged_share must be between 0 and 1")

    rng = random.Random(seed)
    burst = _parse_burst_agent(burst_agent)

    calls: list[PlannedCall] = []
    for _ in range(count):
        prompt = rng.choice(PROMPT_TEMPLATES)

        if burst is not None and rng.random() < burst_share:
            team, agent = burst
            calls.append(_build_call(rng, team, prompt, mock_mode, agent=agent))
            continue

        if rng.random() < untagged_share:
            team = _pick_team(rng, TEAMS)
            model = MOCK_MODEL if mock_mode else _weighted_choice(rng, team.model_weights)
            calls.append(PlannedCall(model=model, prompt=prompt))
            continue

        calls.append(_build_call(rng, _pick_team(rng, TEAMS), prompt, mock_mode))

    return calls


def _parse_burst_agent(burst_agent: str | None) -> tuple[TeamProfile, str] | None:
    """Resolve a ``team/agent`` reference, defaulting to that team's first agent."""
    if burst_agent is None:
        return None

    team_name, _, agent = burst_agent.partition("/")
    team = TEAMS_BY_NAME.get(team_name)
    if team is None:
        known = ", ".join(TEAMS_BY_NAME)
        raise ValueError(f"unknown team {team_name!r} in burst agent; known teams: {known}")

    if agent and agent not in team.agents:
        known_agents = ", ".join(team.agents)
        raise ValueError(f"unknown agent {agent!r} for team {team_name!r}; known: {known_agents}")

    return team, agent or team.agents[0]


def _build_call(
    rng: random.Random,
    team: TeamProfile,
    prompt: str,
    mock_mode: bool,
    agent: str | None = None,
) -> PlannedCall:
    """Build one fully attributed call for a team."""
    model = MOCK_MODEL if mock_mode else _weighted_choice(rng, team.model_weights)
    return PlannedCall(
        model=model,
        prompt=prompt,
        team=team.name,
        project=team.project,
        agent_id=agent or rng.choice(team.agents),
        use_case=rng.choice(team.use_cases),
    )
