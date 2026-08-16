# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffold: `src/` layout, packaging, lint, typecheck, test, and CI tooling.
- Docker Compose stack: Postgres 16 (LiteLLM + FinOpsAI databases), Redis 7, and
  the LiteLLM proxy as the LLM cost-capture point.
- Alembic migrations scoped to the `finops` schema, isolated from LiteLLM's
  Prisma-managed tables.
- Developer verification runbook proving tagged spend logs reach Postgres.
- Cost warehouse schema: `cost_record` (unique on source + dedup key) and
  `collector_watermark`.
- `BaseCollector`: supervised run loop with per-cycle error isolation,
  transactional dedup, watermark advance, and Prometheus counters.
- `LiteLLMSpendCollector`: ingests `LiteLLM_SpendLogs` into `cost_record`,
  attributing spend from request tags and falling back to `unattributed`.
- Collectors service container and multi-stage Dockerfile running as non-root.
- Demo traffic generator: three teams with weighted volume and model mix,
  a deliberate 10% untagged share, `--burst` for a runaway agent, and a
  `--max-spend-usd` ceiling.
- `MockComputeCollector` and `MockVectorDBCollector`: seeded, replay-safe
  simulated infrastructure spend, each documenting the real AWS Cost Explorer
  and Pinecone implementation that replaces it.
- Attribution engine: `aggregate_costs` groups in SQL with per-source
  subtotals and a dimension allowlist; `unattributed_report` quantifies
  ownerless spend and names the models and resources driving it.
- Shared-cost allocation: `even_split`, `usage_weighted` and `fixed_percent`
  strategies with exact largest-remainder splitting, parent/child audit
  records, and rule precedence. See `docs/architecture/allocation.md`.
