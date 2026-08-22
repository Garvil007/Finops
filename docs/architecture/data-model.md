# Data model

## One fact table

Every collector writes into a single table, `finops.cost_record`. One row is one
unit of spend from one source, already attributed to an owner.

| Column | Why it exists |
| --- | --- |
| `source` | `llm`, `compute`, `vectordb`, `infra`. The thing that makes the platform a consolidation rather than a token counter |
| `dedup_key` | The source's own identifier for this unit of spend |
| `occurred_at` | When the spend happened, not when it was ingested |
| `amount_usd` | `Numeric(18, 8)`, never float |
| `quantity` / `unit` | Tokens, GPU-hours, read-units. Lets efficiency be measured separately from volume |
| `team`, `project`, `agent_id`, `use_case` | The attribution dimensions, denormalised onto the row |
| `allocated`, `allocation_parent_id`, `allocation_rule` | The audit trail for shared-cost splits |
| `raw` | The source payload fragment worth keeping, including the `simulated` marker |

### Why dimensions are denormalised

A star schema with a `cost_dimension` table would be the textbook answer. This is
one wide table instead, because every query the product actually serves is a
`GROUP BY` over those four columns, and the join buys nothing until the
cardinality of team/project/agent is large enough to matter. It is not. Rollups
can normalise later if that changes; the migration is additive.

## Deduplication is the load-bearing constraint

```sql
UNIQUE (source, dedup_key)
```

Collectors re-read overlapping windows on purpose. The LiteLLM collector's
watermark window is **inclusive** of its last position, because several spend
logs can share a timestamp and a strict `>` would silently drop every row that
ties the boundary. Re-reading is free precisely because the unique constraint
absorbs it.

That inverts the usual failure mode. Without it, a crash between writing records
and advancing the watermark double-counts spend; with it, the worst case is
re-reading rows that get dropped. Records and watermark also commit in one
transaction, so the window can never advance past unwritten spend.

## Allocation keeps both sides

Shared cost that belongs to no single team is split across the teams that caused
it. The split never overwrites:

- the **parent** is kept exactly as collected and marked `allocated = true`;
- one **child** per team carries that team's share and points back at the parent.

Aggregation excludes allocated parents by default, because the children already
carry the same money. `CostFilters(include_allocated_parents=True)` returns the
raw pre-allocation view for reconciling against the original invoice.

Full strategy comparison and the exact-split arithmetic: [allocation.md](allocation.md).

## Unattributed is a value, not a gap

Spend that arrives with no usable tag is written with `team = "unattributed"`.
It is never dropped and never guessed at. The share of spend nobody owns is a
headline FinOps metric — it has its own dashboard panel and its own API
endpoint, because the number going down is what a tagging rollout is measured by.

Attribution resolves in a fixed order: request tags, then request metadata, then
the LiteLLM virtual key's team alias, then `unattributed`. The key-alias fallback
means a team that issues its own proxy key gets attribution even when its callers
send no tags at all.

## Two databases, one server

| Database | Owner | Access |
| --- | --- | --- |
| `litellm` | The proxy's Prisma migrations | **Read only.** Columns are listed explicitly rather than reflected, so an upstream schema change surfaces as an error instead of silently altering what is ingested |
| `finopsai` | Alembic, in the `finops` schema | Read and write |

`migrations/env.py` pins the Alembic version table to the `finops` schema and
filters autogenerate to objects in it, so a migration can never touch the
proxy's tables even by accident.

The proxy needs a synchronous `postgresql://` URL because Prisma does; the
collector reads the same database over `postgresql+asyncpg://`. Hence two
settings pointing at one database — `LITELLM_DATABASE_URL` and `LITELLM_DB_URL`.

## Money never becomes a float

`Numeric(18, 8)` in the column, `Decimal` in Python, exact arithmetic through
aggregation and allocation. Float rounding is invisible on one row and material
across a million. The only place a float appears is a ratio — utilisation,
percentage change — where it is a display value, not an amount.
