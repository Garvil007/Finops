# FinOpsAI

[![CI](https://github.com/Garvil007/Finops/actions/workflows/ci.yml/badge.svg)](https://github.com/Garvil007/Finops/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Garvil007/Finops/branch/main/graph/badge.svg)](https://codecov.io/gh/Garvil007/Finops)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Cost attribution for AI workloads: consolidates LLM tokens, compute, vector DB, and infrastructure spend into one view, attributed per team, agent, and use case.

## Quickstart

```bash
cp .env.example .env          # set LITELLM_MASTER_KEY (must start with "sk-")
docker compose up -d          # postgres + redis + litellm proxy
alembic upgrade head          # create the finops schema

python -m venv .venv && . .venv/Scripts/activate
pip install -e ".[dev]"
```

The API is then on <http://localhost:8000>, with interactive docs at
<http://localhost:8000/docs>.

Grafana is on <http://localhost:3000> with the FinOpsAI Overview dashboard
provisioned and anonymous viewing enabled; Prometheus is on
<http://localhost:9090>.

- Demo walkthrough: [docs/demo-script.md](docs/demo-script.md)
- Verify the cost-capture path: [docs/dev-verification.md](docs/dev-verification.md)
- Shared-cost allocation: [docs/architecture/allocation.md](docs/architecture/allocation.md)
- Contributing and repository setup: [CONTRIBUTING.md](CONTRIBUTING.md), [docs/dev-setup.md](docs/dev-setup.md)

## Status

Scaffold and infrastructure phases complete. Collectors, attribution engine, API, alerting, and dashboards are in progress; the full README (badges, architecture diagram, config table) lands with the first working pipeline.
