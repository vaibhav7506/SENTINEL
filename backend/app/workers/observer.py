"""Objective demo probes and persisted event detection, independent of injection."""

import asyncio
import logging
import signal
import time
from datetime import UTC, datetime
from uuid import UUID

import httpx
from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.event_loop import create_event_loop
from app.core.logging import configure_logging
from app.db.session import create_database_engine
from app.models import ChaosExperiment, FailureEvent, Host, ServiceObservation

logger = logging.getLogger("sentinel.observer")


def classify(status: int | None, latency: float, slo: float) -> str:
    if status is None:
        return "service_unavailable"
    if status >= 500:
        return "http_error_slo_breach"
    if status != 200:
        return "unexpected_response"
    if latency > slo:
        return "latency_slo_breach"
    return "healthy"


async def observe(engine: AsyncEngine, client: httpx.AsyncClient, settings: Settings) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        host = await session.get(Host, settings.observation_host_id)
        if host is None or host.service_job != "demo-service":
            return
        active = (
            await session.scalars(
                select(ChaosExperiment)
                .where(
                    ChaosExperiment.host_id == host.id,
                    ChaosExperiment.status.in_(["pending", "running", "control_unknown"]),
                )
                .order_by(ChaosExperiment.started_at)
            )
        ).all()
        # Injector TTL is monotonic and survives observer/API failure; process restart clears it.
        if active:
            try:
                response = await client.get(
                    "/control/chaos",
                    headers={
                        "Authorization": "Bearer " + settings.chaos_control_token.get_secret_value()
                    },
                )
                response.raise_for_status()
                state = response.json()
                for experiment in active:
                    if state.get("active") and state.get("experiment_id") == str(experiment.id):
                        experiment.status = "running"
                        experiment.started_at = datetime.fromisoformat(state["started_at"])
                        experiment.parameters = {
                            **experiment.parameters,
                            "injector_expires_at": state["expires_at"],
                        }
                    elif (datetime.now(UTC) - experiment.started_at).total_seconds() > 10:
                        experiment.status = (
                            "completed" if experiment.status == "running" else "unconfirmed"
                        )
                        experiment.ended_at = datetime.now(UTC)
                        events = (
                            await session.scalars(
                                select(FailureEvent).where(
                                    FailureEvent.experiment_id == experiment.id
                                )
                            )
                        ).all()
                        experiment.observed_degradation = {
                            "degradation_confirmed": bool(events),
                            "failure_event_ids": [str(event.id) for event in events],
                            "control_outcome": "inactive_observed",
                        }
                await session.commit()
            except httpx.HTTPError, ValueError, TypeError:
                logger.warning("Injector reconciliation unavailable")
        running_id: UUID | None = next((row.id for row in active if row.status == "running"), None)
        started = datetime.now(UTC)
        timer = time.perf_counter()
        code: int | None = None
        try:
            response = await client.get("/work", params={"units": 100000})
            code = response.status_code
        except httpx.HTTPError:
            pass
        duration = time.perf_counter() - timer
        observed = datetime.now(UTC)
        outcome = classify(code, duration, settings.observation_latency_slo_seconds)
        probe = ServiceObservation(
            host_id=host.id,
            experiment_id=running_id,
            request_started_at=started,
            observed_at=observed,
            latency_seconds=duration,
            status_code=code,
            outcome=outcome,
            breached=outcome != "healthy",
            criteria={
                "latency_slo_seconds": settings.observation_latency_slo_seconds,
                "http_error_status_min": 500,
                "probe": "/work?units=100000",
            },
        )
        session.add(probe)
        await session.flush()
        recent = list(
            (
                await session.scalars(
                    select(ServiceObservation)
                    .where(ServiceObservation.host_id == host.id)
                    .order_by(ServiceObservation.observed_at.desc())
                    .limit(3)
                )
            ).all()
        )
        event = await session.scalar(
            select(FailureEvent).where(
                FailureEvent.host_id == host.id, FailureEvent.recovered_at.is_(None)
            )
        )
        consecutive = (
            len(recent) == 3
            and (recent[0].observed_at - recent[-1].observed_at).total_seconds() <= 15
        )
        if event is None and consecutive and all(row.breached for row in recent):
            first = recent[-1]
            session.add(
                FailureEvent(
                    host_id=host.id,
                    experiment_id=first.experiment_id,
                    failure_type=first.outcome,
                    observed_at=first.observed_at,
                    confirmed_at=observed,
                    criterion={
                        "consecutive_breaches": 3,
                        "max_span_seconds": 15,
                        "latency_slo_seconds": settings.observation_latency_slo_seconds,
                        "http_error_status_min": 500,
                        "probe": "/work?units=100000",
                    },
                    evidence={
                        "probe_ids": [str(row.id) for row in reversed(recent)],
                        "latencies_seconds": [row.latency_seconds for row in reversed(recent)],
                        "status_codes": [row.status_code for row in reversed(recent)],
                    },
                )
            )
        elif event is not None and consecutive and all(not row.breached for row in recent):
            event.recovered_at = recent[-1].observed_at
            event.evidence = {
                **event.evidence,
                "recovery_confirmed_at": observed.isoformat(),
                "recovery_probe_ids": [str(row.id) for row in reversed(recent)],
            }
        await session.commit()


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    registry = CollectorRegistry()
    heartbeat = Gauge(
        "sentinel_observer_heartbeat_timestamp_seconds",
        "Completed observer iteration",
        registry=registry,
    )
    errors = Counter("sentinel_observer_errors", "Failed observer iterations", registry=registry)
    server, thread = start_http_server(8003, registry=registry)
    try:
        async with httpx.AsyncClient(base_url=settings.chaos_demo_url, timeout=3) as client:
            while not stop.is_set():
                try:
                    await observe(engine, client, settings)
                    heartbeat.set(time.time())
                except Exception:
                    errors.inc()
                    logger.error("Observer iteration failed")
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=settings.observation_interval_seconds
                    )
                except TimeoutError:
                    pass
    finally:
        await engine.dispose()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> None:
    asyncio.run(run(), loop_factory=create_event_loop)


if __name__ == "__main__":
    main()
