"""Prometheus metric definitions shared across FinOpsAI services."""

from prometheus_client import Counter

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

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
