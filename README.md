<h1 align="center">FinOpsAI</h1>

<p align="center">
  <em>Cost attribution for AI workloads.</em>
</p>

<p align="center">
  <a href="https://github.com/Garvil007/Finops/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Garvil007/Finops/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://codecov.io/gh/Garvil007/Finops"><img alt="Coverage" src="https://codecov.io/gh/Garvil007/Finops/branch/main/graph/badge.svg"></a>
  <a href="https://www.python.org/downloads/"><img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-blue.svg"></a>
  <a href="https://mypy-lang.org/"><img alt="mypy strict" src="https://img.shields.io/badge/mypy-strict-blue.svg"></a>
  <a href="https://github.com/astral-sh/ruff"><img alt="Ruff" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json"></a>
  <a href="https://github.com/Garvil007/Finops/pkgs/container/finopsai"><img alt="GHCR" src="https://img.shields.io/badge/ghcr.io-finopsai-blue?logo=docker&logoColor=white"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green.svg"></a>
</p>

---

**FinOpsAI consolidates fragmented AI costs — LLM tokens, GPU compute, vector DB, infrastructure — into one attributed, budgeted, alerting cost platform.**

## The problem

Enterprises misestimate the cost of running AI in production by 10–50%, because
the number they track is the one that arrives in a single invoice: the model API
bill. Token cost is the visible tip — the GPU hours behind fine-tuning and
self-hosted inference, the vector database serving retrieval, and the
orchestration infrastructure gluing it together are usually larger, and they only
surface once a workload scales. Those costs fragment across separate invoices
with no shared identifier, so nobody can answer the question that actually
matters: *what does this team, this agent, this use case cost us?*

FinOpsAI is the allocation layer that makes AI spend as attributable as cloud
spend.

## Architecture

```mermaid
flowchart LR
    subgraph apps["AI workloads"]
        A1["Agents and services"]
    end

    subgraph capture["Cost capture"]
        P["LiteLLM Proxy<br/>tags every call"]
        SL[("LiteLLM<br/>SpendLogs")]
        AWS["AWS Cost Explorer<br/>mocked in MVP"]
        PC["Pinecone usage<br/>mocked in MVP"]
        CA["cAdvisor<br/>container metrics"]
    end

    subgraph collect["Collectors"]
        C1["llm_spend"]
        C2["compute"]
        C3["vectordb"]
        C4["infra"]
    end

    subgraph core["Attribution"]
        AE["Attribution engine<br/>tag parsing / allocation / rollups"]
        DB[("PostgreSQL<br/>finops.cost_record")]
    end

    subgraph serve["Serve"]
        API["FastAPI /api/v1"]
        EV["Budget evaluator<br/>every 15 min"]
    end

    subgraph out["Consumers"]
        G["Grafana"]
        S["Slack"]
        PR["Prometheus"]
    end

    A1 -->|tagged LLM calls| P
    P --> SL
    SL --> C1
    AWS --> C2
    PC --> C3
    CA --> C4

    C1 --> AE
    C2 --> AE
    C3 --> AE
    C4 --> AE
    AE --> DB
    DB --> API
    DB --> EV
    API --> G
    DB --> G
    EV -->|threshold crossed| S
    API -.->|metrics| PR
    C1 -.->|metrics| PR
    PR --> G
```

Spend is captured at the point it happens, normalised into one fact table with
shared attribution dimensions, and served from there. Prometheus carries live
signals; Postgres is the source of truth for anything denominated in money,
because counters reset on restart and cannot reconcile to an invoice.

## Screenshots

> Captured from the demo in [docs/demo-script.md](docs/demo-script.md).
> Capture instructions: [docs/images/README.md](docs/images/README.md).

### Grafana — FinOpsAI Overview

<!-- Capture at http://localhost:3000/d/finops-overview after running the demo -->
![Grafana dashboard showing total AI spend, spend by team and source, top cost drivers, budget utilisation, and the unattributed spend trend](docs/images/grafana-overview.png)

### Slack — budget alert

<!-- Capture the alert produced by the traffic generator burst mode -->
![Slack budget alert showing a utilisation bar, top cost drivers, and the projected exhaustion date](docs/images/slack-alert.png)

## Quickstart

Requires Docker and Docker Compose. No API key is needed — the demo runs against
the proxy's offline mock model.

```bash
git clone https://github.com/Garvil007/Finops.git
cd Finops
cp .env.example .env
docker compose up -d --build
```

Wait for the stack to come up, then generate traffic:

```bash
docker compose ps
pip install -e ".[dev]"
python mock_workloads/traffic_generator.py --count 200 --rate 8
```

Open **<http://localhost:3000>** — anonymous viewing is enabled, so there is no
login.

| Service | URL |
| --- | --- |
| Grafana | <http://localhost:3000/d/finops-overview> |
| API docs | <http://localhost:8000/docs> |
| Prometheus | <http://localhost:9090> |
| LiteLLM proxy | <http://localhost:4000> |

Simulated compute and vector DB spend backfills automatically on the collectors'
first cycle, so the dashboard opens with history rather than an empty grid.

To tear the stack down and delete its data volumes, see
[docs/demo-script.md](docs/demo-script.md).

## Features

| | Capability | Notes |
| --- | --- | --- |
| ✅ | **Unified cost model** | One fact table across LLM, compute, vector DB and infrastructure |
| ✅ | **Tag-based attribution** | Team, project, agent, use case — from proxy request tags, with a virtual-key fallback |
| ✅ | **Unattributed spend tracking** | Untagged spend is surfaced, never dropped or guessed at |
| ✅ | **Shared-cost allocation** | Even, usage-weighted and fixed-percent splits, exact to the last unit, parent retained for audit |
| ✅ | **Budgets and alerting** | Per-team thresholds, fire-once-per-crossing with escalation, Slack Block Kit delivery |
| ✅ | **Forecasting** | Run-rate projection to period end with a budget breach date |
| ✅ | **REST API** | Grouped queries, period comparison, timeseries, CRUD, documented OpenAPI |
| ✅ | **Prometheus + Grafana** | Provisioned datasources and an eight-panel dashboard, zero manual import |
| ✅ | **Deduplicated ingestion** | Replay-safe collectors with transactional watermarks |
| ✅ | **Typed codebase** | `mypy --strict`, no untyped call sites |
| ✅ | **CI pipeline** | Lint, typecheck, tests against real Postgres, migration round trip, Docker build |
| ✅ | **85% test coverage** | 204 tests, gate enforced at 80% in CI and locally |
| 🟡 | **Compute and vector DB costs** | Simulated. Real collectors documented, not implemented — see [Roadmap](#roadmap) |
| ⬜ | **Kubernetes cost** | Planned via OpenCost |
| ⬜ | **Anomaly detection** | Planned |

## Configuration

Everything is environment driven via `pydantic-settings`. Copy `.env.example` and
edit; no secret has a real default.

| Variable | Default | Description |
| --- | --- | --- |
| `ENV` | `dev` | `dev` or `prod` |
| `LOG_LEVEL` | `INFO` | Level for structured JSON logs |
| `DATABASE_URL` | `postgresql+asyncpg://finops:finops@localhost:5432/finopsai` | The FinOpsAI warehouse. Async driver |
| `LITELLM_DB_URL` | `postgresql+asyncpg://finops:finops@localhost:5432/litellm` | LiteLLM spend logs, read by the collector. Async driver |
| `LITELLM_DATABASE_URL` | — | Same database, **sync** `postgresql://` URL. Consumed by the proxy container, which is Prisma-based |
| `LITELLM_BASE_URL` | `http://localhost:4000` | Proxy endpoint used by the traffic generator |
| `LITELLM_MASTER_KEY` | _unset_ | Proxy admin key. Must start with `sk-`. **Required** |
| `LITELLM_IMAGE` | `ghcr.io/berriai/litellm:main-stable` | Pin a digest here once verified |
| `REDIS_URL` | `redis://localhost:6379/0` | Alert state storage |
| `API_PORT` | `8000` | Host port for the API |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:8501` | Comma separated. Narrow this for any real deployment |
| `METRICS_PORT` | `9100` | Prometheus scrape port on the worker |
| `ALERT_INTERVAL_MINUTES` | `15` | Budget evaluation cadence |
| `SLACK_WEBHOOK_URL` | _unset_ | Alert destination. Unset logs a warning instead of sending |
| `GRAFANA_DASHBOARD_URL` | _unset_ | Link target on the Slack alert button |
| `ENABLE_MOCK_COLLECTORS` | `true` | Generates simulated infrastructure spend. **Demo only** |
| `DEMO_BACKFILL_DAYS` | `14` | History the mock collectors invent on a cold start |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | `finops` / `changeme` | Database credentials used by Compose |
| `PROMETHEUS_PORT` / `GRAFANA_PORT` | `9090` / `3000` | Host ports |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | Admin login. Anonymous viewing is enabled regardless |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | _unset_ | Only needed to run the generator in live mode |

## Design decisions

The reasoning behind the parts that are not obvious:

- **[Data model](docs/architecture/data-model.md)** — why one denormalised fact
  table instead of a star schema; why the ingestion window is deliberately
  inclusive and re-reads rows; why deduplication rather than exactly-once is the
  load-bearing guarantee; why money never becomes a float.
- **[Shared-cost allocation](docs/architecture/allocation.md)** — the three
  strategies and when each is honest; how splits stay exact to the cent
  (largest-remainder, not per-share rounding); why the parent record is kept and
  excluded from totals rather than overwritten.

Decisions worth stating outright:

| Decision | Reasoning |
| --- | --- |
| Untagged spend becomes `unattributed`, never dropped | The share of spend nobody owns is the metric a tagging rollout is measured by. Hiding it makes the tool look tidy and useless |
| Prometheus for live, Postgres for money | Counters reset on restart. A number that has to reconcile to an invoice cannot come from one |
| Watermark window is inclusive, not exclusive | Spend logs share timestamps; a strict greater-than silently drops rows that tie the boundary. Re-reading is free because dedup absorbs it |
| Allocated parents excluded from totals | Children carry the same money. Counting both doubles the bill |
| Group-by dimensions come from an allowlist | A query parameter must never reach SQL. The test literally sends a query parameter containing a DROP statement |
| Alert state is a high-water mark | Fire-once and escalation fall out of one integer comparison, and month rollover is inherent in the key rather than a reset job that can fail silently |
| Forecast reports its own confidence | A run rate measured over six hours of a month is noise multiplied by thirty. The API says `linear_run_rate` rather than implying precision it does not have |
| SQLite for the suite, Postgres in CI | Hermetic and fast locally; the dialect-specific paths (`date_trunc`, `Numeric`, schemas) get a dedicated Postgres suite in CI |

## Roadmap

- **Real AWS Cost Explorer collector** — the `get_cost_and_usage` call and its
  caveats (tag activation, daily granularity, per-request billing) are already
  documented in `collectors/compute.py`; the mock implements the same interface.
- **Kubernetes cost via OpenCost** — per-namespace and per-workload allocation,
  joined on the same team tags.
- **Anomaly detection on spend** — flag a team or model deviating from its own
  baseline, rather than waiting for a budget threshold to trip.
- **Per-request cost API** — trace a single agent invocation to its fully loaded
  cost, including its share of infrastructure.
- **Multi-currency** — non-USD invoices with rates captured at transaction time,
  not converted at read time.
- **Signed splits** — credits and refunds, which the current allocation
  explicitly rejects rather than mis-rounding.

## Development

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
pre-commit install

ruff check . && ruff format --check . && mypy src/ && pytest
```

Full contributor guide: [CONTRIBUTING.md](CONTRIBUTING.md).
Repository and CI configuration: [docs/dev-setup.md](docs/dev-setup.md).
Verification runbook: [docs/dev-verification.md](docs/dev-verification.md).

## License

[MIT](LICENSE)

## Author

**Garvil Shah** — MS Computer Science

[GitHub](https://github.com/Garvil007) · [LinkedIn](https://www.linkedin.com/in/garvil-shah)
