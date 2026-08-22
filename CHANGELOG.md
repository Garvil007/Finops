# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-22

First working end-to-end slice: spend is captured, attributed, queryable,
alerted on, and visualised.

### Added

#### Cost capture
- Docker Compose stack: Postgres 16 hosting both the LiteLLM spend-log database
  and the FinOpsAI warehouse, Redis, and the LiteLLM proxy as the LLM
  cost-capture point.
- `LiteLLMSpendCollector` reads `LiteLLM_SpendLogs` read-only from an inclusive
  watermark window, deduplicating on `(source, dedup_key)` so a replayed or
  overlapping read cannot double-count spend.
- `BaseCollector`: supervised run loop with per-cycle error isolation,
  transactional dedup and watermark advance, and Prometheus instrumentation.
- `MockComputeCollector` and `MockVectorDBCollector` generate seeded, replay-safe
  simulated infrastructure spend, each documenting the AWS Cost Explorer and
  Pinecone implementation that would replace it behind the same interface.

#### Attribution
- Cost warehouse schema in a dedicated `finops` schema, isolated from LiteLLM's
  Prisma-managed tables.
- Request-tag parsing with a `key:value` convention; spend with no usable tag is
  attributed to `unattributed` rather than dropped.
- `aggregate_costs` groups in SQL with per-source subtotals and a dimension
  allowlist; `unattributed_report` quantifies ownerless spend and names the
  models and resources driving it.
- Shared-cost allocation with `even_split`, `usage_weighted` and `fixed_percent`
  strategies. Splits are exact to the last unit via largest-remainder
  distribution, and the parent record is retained for audit while its children
  carry the money. See [docs/architecture/allocation.md](docs/architecture/allocation.md).

#### API
- REST API with grouped cost queries and period-over-period comparison, the
  unattributed report, bucketed timeseries, team and budget CRUD, a run-rate
  forecast with budget breach estimation, and `/healthz` / `/readyz` probes.
- OpenAPI documentation with per-schema examples, served at `/docs`.
- Request-id logging middleware and Prometheus HTTP metrics.

#### Alerting
- Scheduled budget evaluator comparing period-to-date spend against each active
  budget. A threshold announces once per period and escalates when a higher one
  is crossed; state lives in Redis keyed by month, so rollover needs no reset job.
- Slack Block Kit alerts carrying a utilisation bar, the top three cost drivers,
  a forecast line, and a dashboard link. Logs a warning rather than failing when
  no webhook is configured.

#### Observability
- Consolidated Prometheus metrics: cost ingested, unattributed spend, collector
  runs and lag, budget utilisation, alerts fired, and HTTP request metrics.
- Prometheus and Grafana services with provisioned datasources and an
  auto-loaded eight-panel `FinOpsAI Overview` dashboard.

#### Project
- `src/` layout, `mypy --strict`, `ruff`, `pytest` with an 80% coverage gate,
  and pre-commit hooks.
- GitHub Actions: lint, typecheck, test against a Postgres service container
  with migration round-trip verification, and a Docker build with a smoke test.
- Multi-stage image running as a non-root user with a healthcheck, published to
  GHCR on a `v*` tag.
- Demo traffic generator simulating three teams with weighted volume and model
  mix, a deliberate untagged share, and a `--burst` mode for the alerting demo.
- Runbooks: [demo script](docs/demo-script.md),
  [verification](docs/dev-verification.md), [dev setup](docs/dev-setup.md).

### Known limitations
- `split_amount` rejects negative amounts, so cloud credits and refunds are not
  yet supported.
- Only monthly budgets are evaluated; weekly and daily are stored but skipped.
- Compute and vector DB costs are simulated. The real collectors are documented
  but unimplemented.
- Week bucketing in timeseries degrades to daily on SQLite; Postgres is correct.

[Unreleased]: https://github.com/Garvil007/Finops/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Garvil007/Finops/releases/tag/v0.1.0
