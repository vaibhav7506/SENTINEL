"""Fetch actual host samples and causal service rate evaluations."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from app.features.engineering import Sample, clean_samples
from app.features.schema import HOST_METRICS


@dataclass
class HostSeries:
    name: str
    environment: str
    series: dict[str, list[Sample]] = field(default_factory=dict)
    query_errors: list[str] = field(default_factory=list)


def matrix(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("Unsuccessful Prometheus response")
    data = payload.get("data", {})
    if (
        not isinstance(data, dict)
        or data.get("resultType") != "matrix"
        or not isinstance(data.get("result"), list)
        or not all(isinstance(item, dict) for item in data["result"])
    ):
        raise ValueError("Expected Prometheus matrix")
    return cast(list[dict[str, Any]], data["result"])


def samples_for(item: dict[str, Any], end: float, window: int) -> list[Sample]:
    values = [(float(timestamp), float(value)) for timestamp, value in item["values"]]
    return clean_samples(values, end, window)


class PrometheusSource:
    def __init__(self, client: httpx.AsyncClient, window: int, service_map: dict[str, str]) -> None:
        self.client = client
        self.window = window
        self.service_map = service_map
        self.limit = asyncio.Semaphore(4)

    async def query(
        self, expression: str, end: float, *, range_query: bool = False
    ) -> list[dict[str, Any]]:
        params: dict[str, str | float] = {"query": expression}
        if range_query:
            params.update(start=end - self.window, end=end, step=15)
        else:
            params["time"] = end
        async with self.limit:
            response = await self.client.get(
                "/api/v1/query_range" if range_query else "/api/v1/query", params=params
            )
            response.raise_for_status()
        return matrix(response.json())

    async def collect(self, end: float, freshness: int) -> dict[str, HostSeries]:
        # CPU discovery failure aborts the cycle; no empty windows are synthesized.
        cpu = await self.query(f"sentinel_host_cpu_usage_percent[{self.window}s]", end)
        hosts: dict[str, HostSeries] = {}
        identities: dict[str, tuple[str, str]] = {}
        for item in cpu:
            labels = item["metric"]
            host_id, name, environment = (
                labels["host_id"],
                labels["host_name"],
                labels["environment"],
            )
            if len(host_id) > 128 or len(name) > 256 or len(environment) > 32:
                raise ValueError("Host identity exceeds persistence limits")
            identity = (name, environment)
            if host_id in identities:
                # Duplicate exporters or reused identities must never silently blend samples.
                raise ValueError("Ambiguous host identity")
            identities[host_id] = identity
            samples = samples_for(item, end, self.window)
            if samples and end - samples[-1][0] <= freshness:
                hosts[host_id] = HostSeries(name, environment, {"cpu_usage_percent": samples})
        if len(hosts) > 1000:
            raise ValueError("Host count exceeds worker limit")

        async def host_metric(metric: str) -> None:
            try:
                items = await self.query(f"sentinel_host_{metric}[{self.window}s]", end)
                collected: dict[str, list[Sample]] = {}
                for item in items:
                    labels = item["metric"]
                    host_id = labels.get("host_id")
                    if host_id not in hosts:
                        continue
                    if (labels.get("host_name"), labels.get("environment")) != identities[host_id]:
                        raise ValueError("Inconsistent host identity")
                    if host_id in collected:
                        raise ValueError("Ambiguous metric series")
                    samples = samples_for(item, end, self.window)
                    collected[host_id] = (
                        samples if samples and end - samples[-1][0] <= freshness else []
                    )
                for host_id, samples in collected.items():
                    hosts[host_id].series[metric] = samples
            except httpx.HTTPError, ValueError, KeyError, TypeError:
                for host in hosts.values():
                    host.query_errors.append(metric)

        await asyncio.gather(
            *(host_metric(metric) for metric in HOST_METRICS if metric != "cpu_usage_percent")
        )
        jobs = {self.service_map[host_id] for host_id in hosts if host_id in self.service_map}
        app: dict[str, tuple[dict[str, list[Sample]], list[str]]] = {}

        async def service(job: str) -> None:
            selector = "{job=" + json.dumps(job, ensure_ascii=True) + "}"
            requests = f"sum(rate(sentinel_http_requests_total{selector}[60s]))"
            errors = f"sum(rate(sentinel_http_errors_total{selector}[60s]))"
            latency = (
                "histogram_quantile(0.95,sum by (le) "
                f"(rate(sentinel_http_request_duration_seconds_bucket{selector}[60s])))"
            )
            result: dict[str, list[Sample]] = {}
            failures: list[str] = []
            for name, query in (
                ("request_rate", requests),
                ("error_rate", errors),
                ("latency_p95_seconds", latency),
            ):
                try:
                    items = await self.query(query, end, range_query=True)
                    if len(items) > 1:
                        raise ValueError("Expected one aggregate service series")
                    samples = samples_for(items[0], end, self.window) if items else []
                    # A stale service cannot produce a cross signal.
                    if samples and end - samples[-1][0] <= freshness:
                        result[name] = samples
                except httpx.HTTPError, ValueError, KeyError, TypeError:
                    failures.append(name)
            app[job] = result, failures

        await asyncio.gather(*(service(job) for job in jobs))
        for host_id, host in hosts.items():
            if host_id in self.service_map:
                series, errors = app[self.service_map[host_id]]
                host.series.update(series)
                host.query_errors.extend(errors)
        return hosts
