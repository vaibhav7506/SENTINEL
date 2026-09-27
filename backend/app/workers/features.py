"""Periodic Prometheus -> immutable TimescaleDB feature windows."""

import argparse
import asyncio
import logging
import signal
import time
from datetime import UTC, datetime, timedelta

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.core.event_loop import create_event_loop
from app.core.logging import configure_logging
from app.db.session import create_database_engine
from app.features.engineering import FeatureResult, generate_features
from app.features.prometheus import HostSeries, PrometheusSource
from app.features.schema import FeatureSchema
from app.models import FeatureWindow, Host
from app.services.prometheus import create_prometheus_client

logger = logging.getLogger("sentinel.worker")


async def persist_window(
    engine: AsyncEngine,
    host_id: str,
    host: HostSeries,
    end: datetime,
    schema: FeatureSchema,
    result: FeatureResult,
    service_job: str | None,
) -> bool:
    first_timestamp = host.series["cpu_usage_percent"][0][0]
    last_timestamp = host.series["cpu_usage_percent"][-1][0]
    statement = insert(Host).values(
        id=host_id,
        name=host.name,
        environment=host.environment,
        service_job=service_job,
        first_seen=datetime.fromtimestamp(first_timestamp, UTC),
        last_seen=datetime.fromtimestamp(last_timestamp, UTC),
    )
    statement = statement.on_conflict_do_update(
        index_elements=[Host.id],
        set_={
            "name": statement.excluded.name,
            "environment": statement.excluded.environment,
            "service_job": statement.excluded.service_job,
            "first_seen": func.least(Host.first_seen, statement.excluded.first_seen),
            "last_seen": func.greatest(Host.last_seen, statement.excluded.last_seen),
        },
    )
    async with engine.begin() as connection:
        await connection.execute(statement)
        inserted = await connection.scalar(
            insert(FeatureWindow)
            .values(
                host_id=host_id,
                window_end=end,
                window_start=end - timedelta(seconds=schema.window_seconds),
                schema_version=schema.version,
                schema_hash=schema.digest,
                feature_names=list(schema.names),
                feature_values=result.values,
                features=result.features,
                quality={
                    "metrics": result.quality,
                    "query_errors": sorted(host.query_errors),
                    "service_job": service_job,
                },
            )
            .on_conflict_do_nothing()
            .returning(FeatureWindow.host_id)
        )
    return inserted is not None


def due_tick(now: float, interval: int, previous: int | None) -> int | None:
    """An early timer wake must not repeat a completed minute and skip the next."""
    tick = int(now // interval) * interval
    return tick if previous is None or tick > previous else None


class FeatureWorker:
    def __init__(
        self,
        settings: Settings,
        source: PrometheusSource,
        engine: AsyncEngine,
        registry: CollectorRegistry,
    ) -> None:
        self.settings, self.source, self.engine = settings, source, engine
        self.last_cycle_succeeded = False
        self.schema = FeatureSchema(window_seconds=settings.feature_window_seconds)
        self.heartbeat = Gauge(
            "sentinel_worker_heartbeat_timestamp_seconds",
            "Worker heartbeat time",
            registry=registry,
        )
        self.success = Gauge(
            "sentinel_feature_last_success_timestamp_seconds",
            "Last successful cycle with eligible hosts",
            registry=registry,
        )
        self.worker_success = Gauge(
            "sentinel_worker_last_success_timestamp",
            "Last successful cycle with eligible hosts",
            ["worker"],
            registry=registry,
        ).labels("features")
        self.windows = Counter(
            "sentinel_feature_windows", "Committed feature windows", registry=registry
        )
        self.errors = Counter(
            "sentinel_feature_cycle_errors", "Failed cycles or host transactions", registry=registry
        )
        self.duration = Histogram(
            "sentinel_feature_generation_duration_seconds",
            "Feature cycle duration",
            registry=registry,
        )

    async def cycle(self, end: datetime) -> int:
        self.heartbeat.set(time.time())
        self.last_cycle_succeeded = False
        started = time.perf_counter()
        count = 0
        try:
            hosts = await self.source.collect(
                end.timestamp(), self.settings.feature_freshness_seconds
            )
            failed = False
            for host_id, host in hosts.items():
                try:
                    result = generate_features(host.series, end.timestamp(), self.schema)
                    inserted = await persist_window(
                        self.engine,
                        host_id,
                        host,
                        end,
                        self.schema,
                        result,
                        self.settings.feature_host_service_map.get(host_id),
                    )
                    if inserted:
                        count += 1
                        self.windows.inc()
                except Exception:
                    failed = True
                    self.errors.inc()
                    logger.error("Feature host transaction failed")
            if hosts and not failed:
                self.last_cycle_succeeded = True
                self.success.set(time.time())
                self.worker_success.set(time.time())
            logger.info("Feature cycle complete", extra={"component": "sentinel-worker"})
        except Exception:
            self.errors.inc()
            logger.error("Feature cycle failed")
        finally:
            self.duration.observe(time.perf_counter() - started)
        return count


async def run(*, once: bool = False) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    registry = CollectorRegistry()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
    server, thread = start_http_server(8002, registry=registry)
    try:
        async with create_prometheus_client(
            settings, settings.feature_query_timeout_seconds
        ) as client:
            worker = FeatureWorker(
                settings,
                PrometheusSource(
                    client, settings.feature_window_seconds, settings.feature_host_service_map
                ),
                engine,
                registry,
            )
            previous_tick: int | None = None
            while not stop.is_set():
                now = time.time()
                tick = due_tick(now, settings.feature_interval_seconds, previous_tick)
                if tick is None:
                    assert previous_tick is not None
                    delay = previous_tick + settings.feature_interval_seconds - now
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=max(0.001, delay))
                    except TimeoutError:
                        pass
                    continue
                previous_tick = tick
                await worker.cycle(datetime.fromtimestamp(tick, UTC))
                if once:
                    if not worker.last_cycle_succeeded:
                        raise SystemExit("No successful eligible-host feature cycle")
                    break
                # Preserve bounded aligned windows even when a cycle runs past its interval.
                delay = (
                    settings.feature_interval_seconds
                    - time.time() % settings.feature_interval_seconds
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
    finally:
        await engine.dispose()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        logger.info("Feature worker stopped", extra={"component": "sentinel-worker"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    asyncio.run(run(once=parser.parse_args().once), loop_factory=create_event_loop)


if __name__ == "__main__":
    main()
