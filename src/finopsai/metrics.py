"""Prometheus metric definitions shared across FinOpsAI services."""

from prometheus_client import Counter, Histogram

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
