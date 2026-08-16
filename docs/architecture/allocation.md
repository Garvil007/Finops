# Shared-cost allocation

## The problem

Not every dollar has an owner. A vector database cluster serves three teams from
one bill. The proxy container that routes every LLM call is a single line item.
Tagging cannot fix this, because the cost genuinely is shared.

Leaving that spend in `unattributed` is misleading in both directions: it
overstates the tagging problem, and it understates what each team actually costs
the business. Allocation splits shared cost across the teams that caused it.

## What allocation does not do

Allocation never overwrites or deletes. A split produces:

- the **parent**, kept exactly as collected, marked `allocated = true`;
- one **child** per target team, carrying that team's share, with
  `allocation_parent_id` pointing back at the parent and `allocation_rule`
  naming the rule that produced it.

Totals exclude allocated parents by default, because the children already carry
the same money. Pass `CostFilters(include_allocated_parents=True)` to see the
raw, pre-allocation view — useful for reconciling against the original invoice.

Every allocated dollar therefore traces back to the shared line it came from.
That audit trail is the reason the parent is kept rather than rewritten.

## Strategies

| Strategy | Divides by | Use when | Risk if misapplied |
|---|---|---|---|
| `even_split` | Equal shares | No usage signal exists, or the resource is a fixed platform cost every team benefits from equally | A team that barely uses the resource subsidises heavy users |
| `usage_weighted` | Each team's already-attributed spend in the same period | Consumption tracks usage — shared inference infrastructure, a vector cluster | A team with zero measured usage pays nothing, even if it holds reserved capacity |
| `fixed_percent` | Explicit weights | The split is a business decision, not a measurement — contracted allocations, agreed cost centres | Weights rot silently as usage patterns change |

`usage_weighted` falls back to `even_split` when **every** target has zero usage
in the period. With no signal at all, an even split is more defensible than
allocating nothing.

## Worked example

A shared vector database bills **$100.00** for the day. In the same period the
three teams recorded this attributed LLM spend:

| Team | Attributed LLM spend | Share of usage |
|---|---:|---:|
| search | $60.00 | 60% |
| support-bot | $30.00 | 30% |
| research | $10.00 | 10% |
| **Total** | **$100.00** | **100%** |

The same $100.00 shared cost, split three ways:

| Team | `even_split` | `usage_weighted` | `fixed_percent` (50/25/25) |
|---|---:|---:|---:|
| search | $33.34 | $60.00 | $50.00 |
| support-bot | $33.33 | $30.00 | $25.00 |
| research | $33.33 | $10.00 | $25.00 |
| **Total** | **$100.00** | **$100.00** | **$100.00** |

Note the `even_split` column. A third of $100.00 is $33.333..., which does not
exist in cents. Rounding each share independently gives $99.99 and loses a penny;
rounding each up gives $100.02 and invents two. The split hands the remainder out
one unit at a time, largest fractional part first, so **children always sum to the
parent exactly**. Ties break alphabetically, which makes the result deterministic
rather than dependent on dictionary ordering.

The same arithmetic runs at 8 decimal places for storage, not 2 — the table above
uses cents for legibility.

## Rule precedence

Rules carry a `priority`; the lowest number wins, with the rule name as a
tiebreaker so ordering never depends on how the list was assembled. This lets a
narrow rule override a broad one:

```python
AllocationRule(name="shared-vector-db", source=CostSource.VECTORDB, priority=10, ...)
AllocationRule(name="catch-all-shared", priority=99, ...)
```

A record matches a rule when it is unallocated, is not itself an allocation
child, and matches the rule's `match_team` (default `unattributed`), `source`,
and optional `match_project`. Records matching no rule are left untouched and
counted in `AllocationSummary.skipped_no_rule` — silence about unallocatable
spend would defeat the point.

## Re-running

`apply_allocation_rules` is safe to run repeatedly. An allocated parent no longer
matches any rule, and each child's dedup key is deterministic
(`alloc:{rule}:{parent_dedup_key}:{team}`), so the unique constraint on
`(source, dedup_key)` rejects a duplicate even if the parent flag were lost.

## Known limitations

- **Credits and refunds are not supported.** `split_amount` rejects negative
  amounts rather than guessing at the rounding semantics. A cloud credit
  appearing as negative spend will raise instead of being silently mis-split.
- **Weights come from the same period as the cost.** A shared cost in a period
  where usage is unrepresentative (an outage, a backfill) is split by that
  unrepresentative usage.
- **Rules are code, not configuration.** They live in `DEFAULT_RULES`. Moving
  them into the database is a later step, and would need its own audit trail so
  a changed rule does not silently restate history.
