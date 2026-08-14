# FinOpsAI

Cost attribution for AI workloads: consolidates LLM tokens, compute, vector DB, and infrastructure spend into one view, attributed per team, agent, and use case.

## Quickstart

```bash
cp .env.example .env          # set LITELLM_MASTER_KEY (must start with "sk-")
docker compose up -d          # postgres + redis + litellm proxy
alembic upgrade head          # create the finops schema

python -m venv .venv && . .venv/Scripts/activate
pip install -e ".[dev]"
```

Verify the cost-capture path end to end: [docs/dev-verification.md](docs/dev-verification.md).

## Status

Scaffold and infrastructure phases complete. Collectors, attribution engine, API, alerting, and dashboards are in progress; the full README (badges, architecture diagram, config table) lands with the first working pipeline.
