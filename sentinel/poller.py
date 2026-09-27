import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import aiohttp

from sentinel.config import HealthStatus, ServiceConfig


@dataclass
class HealthResult:
    service_name: str
    timestamp: datetime
    status: HealthStatus
    status_code: int | None
    latency_ms: float | None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status == HealthStatus.SUCCESS


class Poller:
    def __init__(
        self,
        timeout_seconds: float = 5.0,
        concurrent_limit: int = 10,
    ):
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.semaphore = asyncio.Semaphore(concurrent_limit)

    async def poll_all(self, services: list[ServiceConfig]) -> list[HealthResult]:
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            tasks = [self._poll_one(session, svc) for svc in services]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        health_results: list[HealthResult] = []
        for svc, result in zip(services, results):
            if isinstance(result, Exception):
                health_results.append(
                    HealthResult(
                        service_name=svc.name,
                        timestamp=datetime.now(timezone.utc),
                        status=HealthStatus.UNKNOWN,
                        status_code=None,
                        latency_ms=None,
                        error=str(result),
                    )
                )
            else:
                health_results.append(result)

        return health_results

    async def _poll_one(
        self, session: aiohttp.ClientSession, service: ServiceConfig
    ) -> HealthResult:
        async with self.semaphore:
            start = time.perf_counter()
            timestamp = datetime.now(timezone.utc)

            try:
                async with session.get(service.url) as response:
                    latency_ms = (time.perf_counter() - start) * 1000
                    status_code = response.status

                    if response.status == service.expected_status:
                        status = HealthStatus.SUCCESS
                    else:
                        status = HealthStatus.NON_2XX

                    return HealthResult(
                        service_name=service.name,
                        timestamp=timestamp,
                        status=status,
                        status_code=status_code,
                        latency_ms=latency_ms,
                    )

            except asyncio.TimeoutError:
                return HealthResult(
                    service_name=service.name,
                    timestamp=timestamp,
                    status=HealthStatus.TIMEOUT,
                    status_code=None,
                    latency_ms=None,
                    error="Request timeout",
                )

            except aiohttp.ClientConnectorError as e:
                # aiohttp wraps every connector error in "Cannot connect", including DNS errors.
                error_msg = str(e.os_error).lower()
                if "cannot connect" in error_msg or "connection refused" in error_msg:
                    status = HealthStatus.CONNECTION_REFUSED
                elif "name or service not known" in error_msg or "nodename" in error_msg:
                    status = HealthStatus.DNS_FAILURE
                else:
                    status = HealthStatus.UNKNOWN

                return HealthResult(
                    service_name=service.name,
                    timestamp=timestamp,
                    status=status,
                    status_code=None,
                    latency_ms=None,
                    error=str(e),
                )

            except aiohttp.ClientError as e:
                return HealthResult(
                    service_name=service.name,
                    timestamp=timestamp,
                    status=HealthStatus.UNKNOWN,
                    status_code=None,
                    latency_ms=None,
                    error=str(e),
                )

            except Exception as e:
                return HealthResult(
                    service_name=service.name,
                    timestamp=timestamp,
                    status=HealthStatus.UNKNOWN,
                    status_code=None,
                    latency_ms=None,
                    error=f"Unexpected error: {e}",
                )