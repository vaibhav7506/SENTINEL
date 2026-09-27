import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.exposition import CONTENT_TYPE_LATEST


class HttpMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        labels = ["method", "route", "status_code"]
        self.requests = Counter(
            "sentinel_http_requests_total", "HTTP responses", labels, registry=registry
        )
        self.errors = Counter(
            "sentinel_http_errors_total",
            "HTTP 4xx/5xx responses",
            labels,
            registry=registry,
        )
        self.duration = Histogram(
            "sentinel_http_request_duration_seconds",
            "Request duration",
            ["method", "route"],
            registry=registry,
        )
        self.inflight = Gauge(
            "sentinel_http_inflight_requests", "In-flight requests", registry=registry
        )


def instrument(application: FastAPI) -> None:
    registry = CollectorRegistry()
    metrics = HttpMetrics(registry)
    application.state.metrics_registry = registry

    @application.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    @application.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)
        start = time.perf_counter()
        metrics.inflight.inc()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            method = (
                request.method
                if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                else "OTHER"
            )
            metrics.requests.labels(method, route, str(status)).inc()
            if status >= 400:
                metrics.errors.labels(method, route, str(status)).inc()
            metrics.duration.labels(method, route).observe(time.perf_counter() - start)
            metrics.inflight.dec()
