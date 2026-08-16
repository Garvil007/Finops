"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from finopsai.api.middleware import RequestContextMiddleware
from finopsai.api.routes import budgets, costs, forecasts, health, teams
from finopsai.config import Settings, get_settings
from finopsai.logging import configure_logging

API_VERSION = "0.1.0"

DESCRIPTION = """
Cost attribution for AI workloads.

FinOpsAI consolidates the spend that AI systems scatter across separate bills --
LLM API tokens, GPU and CPU compute, vector database usage, and the
infrastructure that orchestrates them -- into one view, attributed per team,
agent and use case.

**What the endpoints answer**

* `/api/v1/costs` -- what did each owner spend, split by source, and how does
  that compare with the window before?
* `/api/v1/costs/unattributed` -- how much spend has no owner at all, and which
  models and resources are causing it?
* `/api/v1/costs/timeseries` -- the same numbers bucketed over time, for
  dashboards.
* `/api/v1/forecast` -- where does this month's run rate land, and when would a
  budget be breached?

**Reading the numbers**

Shared cost that cannot belong to one team is split across the teams that caused
it. The original record is kept for audit and excluded from totals, so allocated
spend is counted exactly once. Spend that could not be attributed is reported as
`unattributed` rather than dropped.
"""

TAGS_METADATA = [
    {"name": "costs", "description": "Query attributed spend."},
    {"name": "forecast", "description": "Project spend and budget breaches."},
    {"name": "budgets", "description": "Spend ceilings per team."},
    {"name": "teams", "description": "Registered cost owners."},
    {"name": "health", "description": "Liveness and readiness probes."},
]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    A factory rather than a module-level singleton so tests can build an app
    with their own settings and dependency overrides.
    """
    active = settings or get_settings()
    configure_logging(active)

    app = FastAPI(
        title="FinOpsAI",
        version=API_VERSION,
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        contact={"name": "Garvil Shah", "url": "https://github.com/Garvil007/Finops"},
        license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=active.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(costs.router)
    app.include_router(forecasts.router)
    app.include_router(budgets.router)
    app.include_router(teams.router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Prometheus scrape endpoint."""
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
