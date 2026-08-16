"""Request logging and Prometheus instrumentation."""

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from finopsai.logging import get_logger
from finopsai.metrics import HTTP_REQUEST_SECONDS, HTTP_REQUESTS

log = get_logger(__name__)

REQUEST_ID_HEADER = "x-request-id"


def _route_template(request: Request) -> str:
    """Path with parameters left as placeholders.

    Using the raw path would create one metric series per team name, which is
    how a metrics backend gets a cardinality problem.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else request.url.path


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request id to the log context and record timing."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - started
            log.exception("request_failed", duration_seconds=round(duration, 4))
            structlog.contextvars.clear_contextvars()
            raise

        duration = time.perf_counter() - started
        template = _route_template(request)

        HTTP_REQUESTS.labels(
            method=request.method, path=template, status=str(response.status_code)
        ).inc()
        HTTP_REQUEST_SECONDS.labels(method=request.method, path=template).observe(duration)

        log.info(
            "request_complete",
            status=response.status_code,
            duration_seconds=round(duration, 4),
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        structlog.contextvars.clear_contextvars()
        return response
