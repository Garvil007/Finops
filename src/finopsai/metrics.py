"""Prometheus metrics for every FinOpsAI service.

All metric definitions live here rather than beside the code that updates them,
so the names, labels and cardinality of the whole surface can be reviewed in one
place. Labels are deliberately low cardinality: team, source, collector and
route template are bounded sets, while model names, request paths and dedup keys
are not and never become labels.

Prometheus answers "what is happening now" for live panels and alerting.
Postgres answers "what did we spend" for anything that must reconcile to a
number -- counters reset when a process restarts, so the warehouse is the source
of truth for money.
"""

from prometheus_client import Counter, Gauge, Histogram

# --- Cost ingestion -------------------------------------------------------

COST_USD = Counter(
    "finopsai_cost_usd_total",
    "Spend ingested into the warehouse, in USD.",
    ["source", "team"],
)

UNATTRIBUTED_USD = Counter(
    "finopsai_unattributed_usd_total",
    "Spend ingested with no owning team, in USD. The tagging debt.",
)

# --- Collectors -----------------------------------------------------------

COLLECTOR_RUNS = Counter(
    "finopsai_collector_runs_total",
    "Collector cycles, labelled by outcome.",
    ["collector", "status"],
)

COLLECTOR_RECORDS = Counter(
    "finopsai_collector_records_total",
    "Cost records persisted, excluding rows dropped as duplicates.",
    ["collector"],
)

COLLECTOR_LAG_SECONDS = Gauge(
    "finopsai_collector_lag_seconds",
    "Age of the newest record a collector has ingested. Rises when a source stalls.",
    ["collector"],
)

# --- Budgets --------------------------------------------------------------

BUDGET_UTILIZATION = Gauge(
    "finopsai_budget_utilization",
    "Period-to-date spend as a fraction of the team's budget.",
    ["team"],
)

ALERTS_FIRED = Counter(
    "finopsai_alerts_fired_total",
    "Budget alerts announced, by team and threshold.",
    ["team", "threshold"],
)

# --- API ------------------------------------------------------------------

HTTP_REQUESTS = Counter(
    "finopsai_http_requests_total",
    "HTTP requests, labelled by route template rather than raw path.",
    ["method", "path", "status"],
)

HTTP_REQUEST_SECONDS = Histogram(
    "finopsai_http_request_duration_seconds",
    "HTTP request duration.",
    ["method", "path"],
)

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
