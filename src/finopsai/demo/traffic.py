"""Drive demo traffic through the LiteLLM proxy.

Two modes:

``mock``
    Every call targets the proxy's ``mock-gpt`` model, which answers without
    contacting a provider. Free, offline, no API key.

``live``
    Calls target the real cheap models in the profiles. A short demo run costs
    a few cents; ``--max-spend-usd`` aborts before that can drift.

Whether ``mock`` mode produces a non-zero ``spend`` in LiteLLM_SpendLogs has not
been verified against a running proxy. If a mock run leaves every cost at zero,
the dashboard has no LLM story and ``--mode live`` is the one to demo with.

Untagged calls are deliberately sent with the master key rather than a team key.
LiteLLM stamps its virtual key's team alias onto every request, so sending them
with a team key would attribute them anyway and destroy the unattributed slice
this generator exists to produce.
"""

import argparse
import asyncio
import contextlib
from dataclasses import dataclass, field
from decimal import Decimal

import httpx

from finopsai.demo.workloads import TEAMS, PlannedCall, plan_calls
from finopsai.logging import get_logger

log = get_logger(__name__)

COST_HEADER = "x-litellm-response-cost"
DEFAULT_MAX_SPEND_USD = Decimal("1.00")
DEFAULT_RATE = 5.0
DEFAULT_COUNT = 200
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_TOKENS = 64
REQUEST_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class TrafficOptions:
    """Everything the generator needs for one run."""

    base_url: str
    master_key: str
    count: int = DEFAULT_COUNT
    rate: float = DEFAULT_RATE
    seed: int = 42
    mock_mode: bool = True
    burst_agent: str | None = None
    max_spend_usd: Decimal = DEFAULT_MAX_SPEND_USD
    concurrency: int = DEFAULT_CONCURRENCY
    max_tokens: int = DEFAULT_MAX_TOKENS


@dataclass
class TrafficReport:
    """Outcome of a run."""

    sent: int = 0
    failed: int = 0
    untagged: int = 0
    spend_usd: Decimal = field(default_factory=lambda: Decimal(0))
    aborted_reason: str | None = None


async def ensure_team_keys(client: httpx.AsyncClient, options: TrafficOptions) -> dict[str, str]:
    """Mint one virtual key per team, falling back to the master key.

    A per-team key is what makes the LiteLLM team-alias fallback meaningful, so
    the demo exercises attribution by key as well as by tag.
    """
    keys: dict[str, str] = {}
    for team in TEAMS:
        payload = {"key_alias": f"team-{team.name}", "metadata": {"team": team.name}}
        try:
            response = await client.post(
                "/key/generate",
                json=payload,
                headers={"Authorization": f"Bearer {options.master_key}"},
            )
            response.raise_for_status()
            keys[team.name] = str(response.json()["key"])
        except (httpx.HTTPError, KeyError, ValueError) as error:
            # A duplicate alias from an earlier run is the common case here.
            log.warning("team_key_unavailable", team=team.name, error=str(error))
            keys[team.name] = options.master_key
    return keys


def build_request_body(call: PlannedCall, max_tokens: int) -> dict[str, object]:
    """Render one chat-completion body, with tags only when the call is tagged."""
    body: dict[str, object] = {
        "model": call.model,
        "messages": [{"role": "user", "content": call.prompt}],
        "max_tokens": max_tokens,
    }
    if call.is_tagged:
        body["metadata"] = {"tags": call.tags()}
    return body


def response_cost(response: httpx.Response) -> Decimal:
    """Read the per-request cost LiteLLM reports, if it reports one."""
    raw = response.headers.get(COST_HEADER)
    if raw is None:
        return Decimal(0)
    try:
        return Decimal(raw)
    except ArithmeticError:
        return Decimal(0)


async def _send_one(
    client: httpx.AsyncClient,
    call: PlannedCall,
    api_key: str,
    options: TrafficOptions,
    report: TrafficReport,
) -> None:
    """Send one call and fold its outcome into the report."""
    try:
        response = await client.post(
            "/v1/chat/completions",
            json=build_request_body(call, options.max_tokens),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        report.failed += 1
        log.warning("demo_call_failed", model=call.model, error=str(error))
        return

    report.sent += 1
    report.spend_usd += response_cost(response)
    if not call.is_tagged:
        report.untagged += 1


async def run_traffic(
    options: TrafficOptions, client: httpx.AsyncClient | None = None
) -> TrafficReport:
    """Send the planned calls, pacing to the requested rate."""
    calls = plan_calls(
        options.count,
        options.seed,
        mock_mode=options.mock_mode,
        burst_agent=options.burst_agent,
    )
    report = TrafficReport()

    owns_client = client is None
    active = client or httpx.AsyncClient(base_url=options.base_url, timeout=REQUEST_TIMEOUT_SECONDS)

    try:
        keys = await ensure_team_keys(active, options)
        semaphore = asyncio.Semaphore(options.concurrency)
        delay = 1.0 / options.rate if options.rate > 0 else 0.0

        def over_budget() -> bool:
            return report.spend_usd >= options.max_spend_usd

        async def dispatch(call: PlannedCall) -> None:
            async with semaphore:
                # Checked here as well as in the producer: without pacing the
                # producer queues every task before the first cost is reported,
                # so the producer-side check alone would never trip.
                if over_budget():
                    report.aborted_reason = "max_spend_reached"
                    return
                # Untagged calls use the master key on purpose; see module docstring.
                api_key = keys.get(call.team or "", options.master_key)
                await _send_one(active, call, api_key, options, report)

        async with asyncio.TaskGroup() as group:
            for call in calls:
                if over_budget():
                    report.aborted_reason = "max_spend_reached"
                    log.warning(
                        "demo_traffic_aborted",
                        spend_usd=str(report.spend_usd),
                        limit_usd=str(options.max_spend_usd),
                    )
                    break
                group.create_task(dispatch(call))
                if delay:
                    await asyncio.sleep(delay)
    finally:
        if owns_client:
            await active.aclose()

    log.info(
        "demo_traffic_complete",
        sent=report.sent,
        failed=report.failed,
        untagged=report.untagged,
        spend_usd=str(report.spend_usd),
        aborted_reason=report.aborted_reason,
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface for the generator."""
    parser = argparse.ArgumentParser(
        prog="traffic_generator",
        description="Send tagged demo traffic through the LiteLLM proxy.",
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="calls to send")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, help="calls per second")
    parser.add_argument("--seed", type=int, default=42, help="makes a run reproducible")
    parser.add_argument(
        "--mode",
        choices=("mock", "live"),
        default="mock",
        help="mock uses the offline model; live calls real providers",
    )
    parser.add_argument(
        "--burst",
        nargs="?",
        const="research/experiment-planner",
        default=None,
        metavar="TEAM/AGENT",
        help="give one agent the majority of traffic, simulating a runaway agent",
    )
    parser.add_argument(
        "--max-spend-usd",
        type=Decimal,
        default=DEFAULT_MAX_SPEND_USD,
        help="abort once reported spend reaches this amount",
    )
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    return parser


def options_from_args(args: argparse.Namespace, base_url: str, master_key: str) -> TrafficOptions:
    """Translate parsed arguments into run options."""
    return TrafficOptions(
        base_url=base_url,
        master_key=master_key,
        count=args.count,
        rate=args.rate,
        seed=args.seed,
        mock_mode=args.mode == "mock",
        burst_agent=args.burst,
        max_spend_usd=args.max_spend_usd,
        concurrency=args.concurrency,
    )


def main(argv: list[str] | None = None) -> None:
    """Entrypoint used by the mock_workloads CLI shim."""
    from finopsai.config import get_settings
    from finopsai.logging import configure_logging

    settings = get_settings()
    configure_logging(settings)

    if settings.litellm_master_key is None:
        raise SystemExit("LITELLM_MASTER_KEY is not set; the proxy will reject every call")

    args = build_parser().parse_args(argv)
    options = options_from_args(
        args,
        base_url=settings.litellm_base_url,
        master_key=settings.litellm_master_key.get_secret_value(),
    )

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_traffic(options))


if __name__ == "__main__":
    main()
