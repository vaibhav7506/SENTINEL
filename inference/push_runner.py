"""Persistent Python 3.14 scoring subprocess for the supported Celery runtime.

Only JSON task identifiers cross stdio. Raw telemetry and secrets stay in this process.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
from prometheus_client import CollectorRegistry
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.session import create_database_engine
from app.features.engineering import generate_features
from app.features.schema import FeatureSchema
from app.models import FeatureWindow, Host, ModelVersion, Prediction
from app.platform_metrics import snapshot
from app.saas.models import AgentCredential, MetricSample
from app.saas.repository import account_session

if TYPE_CHECKING:
    from inference.scoring import Scorer


class Runner:
    def __init__(self) -> None:
        self.settings = Settings()
        self.engine = create_database_engine(self.settings)
        self.scorer: Scorer | None = None
        self.client = httpx.AsyncClient(follow_redirects=False, trust_env=False)

    async def due(self) -> list[dict[str, str]]:
        now = datetime.now(UTC)
        # Trusted scheduler enumerates only credential-enrolled hosts. Individual jobs
        # repeat ownership validation under a non-owner RLS role.
        async with async_sessionmaker(self.engine)() as db:
            rows = (
                await db.execute(
                    select(Host.account_id, Host.id)
                    .where(
                        Host.status == "active",
                        Host.last_seen >= now - timedelta(seconds=3000),
                        select(AgentCredential.id)
                        .where(
                            AgentCredential.host_id == Host.id,
                            AgentCredential.revoked_at.is_(None),
                        )
                        .exists(),
                        Host.last_seen
                        >= func.now()
                        - Host.reporting_interval_seconds
                        * self.settings.agent_stale_intervals
                        * text("interval '1 second'"),
                    )
                    .order_by(Host.last_seen.desc(), Host.id)
                    .limit(1000)
                )
            ).all()
        end = datetime.fromtimestamp(
            int(now.timestamp())
            // self.settings.feature_interval_seconds
            * self.settings.feature_interval_seconds,
            UTC,
        )
        return [
            {"account_id": str(a), "host_id": h, "window_end": end.isoformat()} for a, h in rows
        ]

    async def infer(self, task: dict[str, str]) -> dict[str, Any]:
        from inference.scoring import ROOT, Scorer
        from inference.worker import Worker

        account = UUID(task["account_id"])
        host_id = str(task["host_id"])
        end = datetime.fromisoformat(task["window_end"])
        now = datetime.now(UTC)
        if (
            end.tzinfo is None
            or not 0 <= (now - end).total_seconds() <= self.settings.inference_max_age_seconds
        ):
            return {"status": "stale", "predictions": 0}
        # Ownership is checked BEFORE reading telemetry or loading ML.
        async with account_session(self.engine, account) as db:
            host = await db.scalar(select(Host).where(Host.id == host_id))
            if host is None or host.status != "active":
                return {"status": "host_unavailable", "predictions": 0}
        if self.scorer is None:
            self.scorer = Scorer(ROOT / self.settings.inference_model_path)
        schema_meta = self.scorer.metadata["feature_schema"]
        schema = FeatureSchema(window_seconds=schema_meta["window_seconds"])
        if schema.digest != schema_meta["hash"] or list(schema.names) != schema_meta["names"]:
            raise ValueError("Frozen feature contract mismatch")
        # A session lease spans both feature storage and the prediction transaction.
        async with self.engine.connect() as lease:
            lock = await lease.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:host,17))"),
                {"host": str(account) + ":" + host_id},
            )
            await lease.commit()
            if not lock:
                raise RuntimeError("Host processing lease busy")
            try:
                async with account_session(self.engine, account) as db:
                    model = await db.scalar(
                        select(ModelVersion).where(
                            ModelVersion.version == self.scorer.metadata["version"]
                        )
                    )
                    if model is None or model.training_metadata != self.scorer.metadata:
                        raise ValueError("Frozen model registration missing")
                    existing = await db.scalar(
                        select(Prediction.id).where(
                            Prediction.host_id == host_id,
                            Prediction.model_version_id == model.id,
                            Prediction.feature_window_end == end,
                        )
                    )
                    if existing:
                        return {"status": "duplicate", "predictions": 0}
                    start = end - timedelta(seconds=schema.window_seconds)
                    samples = (
                        await db.scalars(
                            select(MetricSample)
                            .where(
                                MetricSample.host_id == host_id,
                                MetricSample.observed_at > start,
                                MetricSample.observed_at <= end,
                            )
                            .order_by(MetricSample.observed_at)
                            .limit(1801)
                        )
                    ).all()
                    if len(samples) > 1800:
                        return {"status": "excessive_sampling", "predictions": 0}
                    series: dict[str, list[tuple[float, float]]] = {}
                    for sample in samples:
                        for metric, value in sample.values.items():
                            series.setdefault(metric, []).append(
                                (sample.observed_at.timestamp(), value)
                            )
                    result = generate_features(series, end.timestamp(), schema)
                    cpu_quality = result.quality.get("cpu_usage_percent", {})
                    if not isinstance(cpu_quality, dict) or cpu_quality.get(
                        "coverage_seconds", 0
                    ) < (0.8 * schema.window_seconds):
                        return {"status": "insufficient_coverage", "predictions": 0}
                    await db.execute(
                        insert(FeatureWindow)
                        .values(
                            account_id=account,
                            host_id=host_id,
                            window_start=start,
                            window_end=end,
                            schema_version=schema.version,
                            schema_hash=schema.digest,
                            feature_names=list(schema.names),
                            feature_values=result.values,
                            features=result.features,
                            quality={
                                "metrics": result.quality,
                                "source": "authenticated_push_v1",
                                "sample_count": len(samples),
                            },
                        )
                        .on_conflict_do_nothing()
                    )
                    await db.commit()
                registry = CollectorRegistry()
                worker = Worker(
                    self.settings,
                    self.engine,
                    self.scorer,
                    self.client,
                    registry,
                    account_id=account,
                    host_id=host_id,
                    window_end=end,
                )
                worker.model_id = model.id
                count = await worker.score_windows()
                await worker.correlate()
                # Proposals stay scoped and mock-only; approval never triggers execution.
                await worker.proposals.cycle()
                return {
                    "status": "scored" if count else "insufficient_coverage",
                    "predictions": count,
                    "_platform": snapshot(registry),
                }
            finally:
                await lease.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:host,17))"),
                    {"host": str(account) + ":" + host_id},
                )
                await lease.commit()

    async def deliver(self) -> dict[str, Any]:
        from app.models import Alert
        from inference.scoring import ROOT, Scorer
        from inference.worker import Worker

        async with async_sessionmaker(self.engine)() as db:
            accounts = (
                await db.scalars(
                    select(Alert.account_id)
                    .where(
                        Alert.status.in_(["pending", "retry", "sending"]),
                        Alert.next_attempt_at <= func.now(),
                    )
                    .distinct()
                    .limit(100)
                )
            ).all()
        if accounts and self.scorer is None:
            self.scorer = Scorer(ROOT / self.settings.inference_model_path)
        aggregate: dict[str, float] = {}
        for account in accounts:
            assert self.scorer is not None
            registry = CollectorRegistry()
            worker = Worker(
                self.settings,
                self.engine,
                self.scorer,
                self.client,
                registry,
                account_id=account,
            )
            await worker.initialize()
            await worker.correlate()
            await worker.send_pending()
            await worker.correlate()
            for key, value in snapshot(registry).items():
                aggregate[key] = aggregate.get(key, 0) + value
        return {"accounts": len(accounts), "_platform": aggregate}

    async def close(self) -> None:
        await self.client.aclose()
        await self.engine.dispose()


def main() -> None:
    runner = Runner()
    with asyncio.Runner() as loop:
        try:
            for line in sys.stdin:
                try:
                    if len(line) > 4096:
                        raise ValueError("Task envelope too large")
                    task = json.loads(line)
                    operation = task["operation"]
                    result: Any
                    if operation == "due":
                        result = loop.run(runner.due())
                    elif operation == "infer":
                        result = loop.run(runner.infer(task))
                    elif operation == "deliver":
                        result = loop.run(runner.deliver())
                    else:
                        raise ValueError("Unknown task")
                    print(json.dumps({"ok": True, "result": result}), flush=True)
                except Exception:
                    # Never echo database URLs, broker credentials or raw telemetry.
                    print(
                        json.dumps({"ok": False, "error": "Task processing failed"}),
                        flush=True,
                    )
        finally:
            loop.run(runner.close())


if __name__ == "__main__":
    main()
