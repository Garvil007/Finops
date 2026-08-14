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
