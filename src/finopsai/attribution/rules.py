"""Allocation of shared cost.

Some spend genuinely belongs to nobody in particular. The vector database
cluster serves every team; the proxy container itself bills as one line. Leaving
that cost in ``unattributed`` overstates the tagging problem and understates
what each team actually costs the business, so it is split across the teams that
caused it.

Three strategies, in increasing order of how much they need to know:

``even_split``
    Divide equally. Honest when the shared resource has no usage signal.

``usage_weighted``
    Divide in proportion to what each team already spent in the same period.
    A team running 60% of the tokens carries 60% of the shared infrastructure.

``fixed_percent``
    Explicit weights, for cost that is governed by contract rather than usage.

Splitting never overwrites. The parent record is kept and marked ``allocated``,
which removes it from totals, and child records carry the per-team amounts with
a pointer back to the parent. Every allocated dollar can be traced to the shared
line it came from.

The split is exact. Children always sum to the parent to the last unit, using
largest-remainder distribution rather than rounding each share independently --
three ways of splitting ten dollars is 3.34 / 3.33 / 3.33, never 3.33 x 3.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from finopsai.attribution.engine import Period
from finopsai.attribution.models import CostRecord, CostSource
from finopsai.attribution.tags import UNATTRIBUTED
from finopsai.logging import get_logger

log = get_logger(__name__)

ZERO = Decimal(0)
STEP = Decimal("0.00000001")
QUANTITY_STEP = Decimal("0.0001")


class AllocationStrategy(StrEnum):
    """How a shared cost is divided."""

    EVEN_SPLIT = "even_split"
    USAGE_WEIGHTED = "usage_weighted"
    FIXED_PERCENT = "fixed_percent"


@dataclass(frozen=True)
class AllocationRule:
    """Which shared records to split, across whom, and how.

    Rules are evaluated in ascending ``priority``; the first match wins, so a
    narrow rule with a low number beats a broad catch-all.
    """

    name: str
    strategy: AllocationStrategy
    targets: tuple[str, ...]
    source: CostSource | None = None
    match_team: str = UNATTRIBUTED
    match_project: str | None = None
    weights: Mapping[str, Decimal] | None = None
    usage_sources: tuple[CostSource, ...] = (CostSource.LLM,)
    priority: int = 100

    def __post_init__(self) -> None:
        if not self.targets:
            raise ValueError(f"rule {self.name!r} must name at least one target team")
        if self.strategy is AllocationStrategy.FIXED_PERCENT:
            if not self.weights:
                raise ValueError(f"rule {self.name!r} is fixed_percent but has no weights")
            missing = set(self.targets) - set(self.weights)
            if missing:
                raise ValueError(f"rule {self.name!r} has no weight for: {sorted(missing)}")

    def matches(self, record: CostRecord) -> bool:
        """True when this rule governs the given record."""
        if record.allocated or record.is_allocation_child:
            return False
        if record.team != self.match_team:
            return False
        if self.source is not None and record.source is not self.source:
            return False
        return not (self.match_project is not None and record.project != self.match_project)


DEFAULT_RULES: tuple[AllocationRule, ...] = (
    AllocationRule(
        name="shared-vector-db",
        strategy=AllocationStrategy.USAGE_WEIGHTED,
        source=CostSource.VECTORDB,
        targets=("search", "support-bot", "research"),
        priority=10,
    ),
    AllocationRule(
        name="shared-platform-infra",
        strategy=AllocationStrategy.EVEN_SPLIT,
        source=CostSource.INFRA,
        targets=("search", "support-bot", "research"),
        priority=20,
    ),
)


@dataclass
class AllocationSummary:
    """What one allocation pass did."""

    parents_allocated: int = 0
    children_created: int = 0
    amount_allocated: Decimal = field(default_factory=lambda: Decimal(0))
    skipped_no_rule: int = 0


def even_weights(targets: Sequence[str]) -> dict[str, Decimal]:
    """Equal weight per target."""
    return {target: Decimal(1) for target in targets}


def split_amount(
    amount: Decimal, weights: Mapping[str, Decimal], step: Decimal = STEP
) -> dict[str, Decimal]:
    """Divide ``amount`` by weight so the shares sum to it exactly.

    Rounding each share on its own loses or invents money -- a third of ten
    dollars, rounded three times, is 9.99. Instead every share is rounded down
    and the remainder is handed out one step at a time, largest fractional part
    first, with ties broken by name so the result is deterministic.
    """
    if amount < ZERO:
        raise ValueError("split_amount does not handle credits; amount must not be negative")
    if not weights:
        raise ValueError("cannot split without weights")

    total_weight = sum(weights.values())
    if total_weight <= ZERO:
        raise ValueError("total weight must be positive")

    exact = {name: amount * weight / total_weight for name, weight in weights.items()}
    shares = {name: value.quantize(step, rounding=ROUND_DOWN) for name, value in exact.items()}

    remainder = amount - sum(shares.values())
    leftover_steps = int((remainder / step).to_integral_value(rounding=ROUND_DOWN))

    if leftover_steps > 0:
        ranked = sorted(
            shares,
            key=lambda name: (-(exact[name] - shares[name]), name),
        )
        for name in ranked[:leftover_steps]:
            shares[name] += step

    return shares


async def usage_weights(
    session: AsyncSession,
    period: Period,
    targets: Sequence[str],
    sources: Sequence[CostSource],
) -> dict[str, Decimal]:
    """Weight each target by what it already spent in the period.

    A team with no usage would otherwise be handed a zero share and effectively
    excluded. When *every* target has zero usage there is no signal at all, so
    the split falls back to even rather than failing.
    """
    query = (
        sa.select(
            CostRecord.team,
            sa.cast(sa.func.coalesce(sa.func.sum(CostRecord.amount_usd), 0), sa.Numeric(18, 8)),
        )
        .where(
            CostRecord.occurred_at >= period.start,
            CostRecord.occurred_at < period.end,
            CostRecord.allocated.is_(False),
            CostRecord.team.in_(list(targets)),
            CostRecord.source.in_(list(sources)),
        )
        .group_by(CostRecord.team)
    )

    rows = (await session.execute(query)).all()
    observed = {str(row[0]): Decimal(row[1] or 0) for row in rows}
    weights = {target: observed.get(target, ZERO) for target in targets}

    if sum(weights.values()) <= ZERO:
        log.info("usage_weights_empty_falling_back_to_even", targets=list(targets))
        return even_weights(targets)

    return weights


def select_rule(record: CostRecord, rules: Sequence[AllocationRule]) -> AllocationRule | None:
    """Return the highest-precedence rule matching a record, if any."""
    for rule in sorted(rules, key=lambda candidate: (candidate.priority, candidate.name)):
        if rule.matches(record):
            return rule
    return None


async def resolve_weights(
    session: AsyncSession, rule: AllocationRule, period: Period
) -> dict[str, Decimal]:
    """Compute the weights a rule implies for this period."""
    if rule.strategy is AllocationStrategy.EVEN_SPLIT:
        return even_weights(rule.targets)
    if rule.strategy is AllocationStrategy.FIXED_PERCENT:
        assert rule.weights is not None  # guaranteed by __post_init__
        return {target: Decimal(rule.weights[target]) for target in rule.targets}
    return await usage_weights(session, period, rule.targets, rule.usage_sources)


def build_children(
    parent: CostRecord, rule: AllocationRule, shares: Mapping[str, Decimal]
) -> list[CostRecord]:
    """Create one child record per target team."""
    quantity_shares: Mapping[str, Decimal] = {}
    if parent.quantity is not None:
        quantity_shares = split_amount(
            parent.quantity,
            {team: shares[team] or STEP for team in shares},
            step=QUANTITY_STEP,
        )

    children: list[CostRecord] = []
    for team, amount in shares.items():
        children.append(
            CostRecord(
                source=parent.source,
                dedup_key=f"alloc:{rule.name}:{parent.dedup_key}:{team}",
                occurred_at=parent.occurred_at,
                amount_usd=amount,
                quantity=quantity_shares.get(team),
                unit=parent.unit,
                model=parent.model,
                team=team,
                project=parent.project,
                agent_id=parent.agent_id,
                use_case=parent.use_case,
                allocation_parent_id=parent.id,
                allocation_rule=rule.name,
                raw={
                    "allocated_from": parent.dedup_key,
                    "strategy": str(rule.strategy),
                    "rule": rule.name,
                },
            )
        )
    return children


async def apply_allocation_rules(
    session: AsyncSession,
    period: Period,
    rules: Sequence[AllocationRule] = DEFAULT_RULES,
) -> AllocationSummary:
    """Split every matching shared record in the period across its target teams.

    Safe to re-run: an allocated parent no longer matches any rule, and each
    child carries a deterministic dedup key that the unique constraint rejects
    on a second pass.
    """
    candidates = (
        await session.scalars(
            sa.select(CostRecord).where(
                CostRecord.occurred_at >= period.start,
                CostRecord.occurred_at < period.end,
                CostRecord.allocated.is_(False),
                CostRecord.allocation_parent_id.is_(None),
            )
        )
    ).all()

    summary = AllocationSummary()

    for parent in candidates:
        rule = select_rule(parent, rules)
        if rule is None:
            summary.skipped_no_rule += 1
            continue

        weights = await resolve_weights(session, rule, period)
        shares = split_amount(parent.amount_usd, weights)
        children = build_children(parent, rule, shares)

        session.add_all(children)
        parent.allocated = True
        parent.allocation_rule = rule.name

        summary.parents_allocated += 1
        summary.children_created += len(children)
        summary.amount_allocated += parent.amount_usd

    await session.flush()
    log.info(
        "allocation_pass_complete",
        parents_allocated=summary.parents_allocated,
        children_created=summary.children_created,
        amount_allocated=str(summary.amount_allocated),
        skipped_no_rule=summary.skipped_no_rule,
    )
    return summary
