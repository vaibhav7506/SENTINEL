# ruff: noqa: E402
"""Read-only live acceptance: observe real feature window growth over 75 seconds.

Run with backend/.venv/Scripts/python.exe scripts/verify_features.py.
"""

import asyncio
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.features.schema import APP_METRICS, FeatureSchema
from app.models import FeatureWindow, Host


async def main() -> None:
    settings = Settings(_env_file=ROOT / ".env")
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    schema = FeatureSchema(window_seconds=settings.feature_window_seconds)
    engine = create_database_engine(settings)
    session_factory = async_sessionmaker(engine)
    try:
        async with session_factory() as session:
            start_count = await session.scalar(select(func.count()).select_from(FeatureWindow))
        print(
            f"Observed {start_count} windows; watching the live worker for 75 seconds",
            flush=True,
        )
        await asyncio.sleep(75)
        async with session_factory() as session:
            end_count = await session.scalar(select(func.count()).select_from(FeatureWindow))
            assert end_count is not None and start_count is not None and end_count > start_count
            hypertable = await session.scalar(
                text(
                    "SELECT count(*) FROM timescaledb_information.hypertables "
                    "WHERE hypertable_name='feature_windows'"
                )
            )
            assert hypertable == 1
            hosts = list((await session.scalars(select(Host))).all())
            assert hosts
            summaries = []
            now = datetime.now(UTC)
            for host in hosts:
                rows = list(
                    (
                        await session.scalars(
                            select(FeatureWindow)
                            .where(FeatureWindow.host_id == host.id)
                            .order_by(FeatureWindow.window_end.desc())
                            .limit(3)
                        )
                    ).all()
                )
                assert len(rows) >= 2
                latest = rows[0]
                assert (
                    (now - latest.window_end).total_seconds()
                    < settings.feature_interval_seconds * 2 + settings.feature_freshness_seconds
                )
                assert latest.schema_hash == schema.digest
                assert latest.schema_version == schema.version
                assert latest.feature_names == list(schema.names)
                assert latest.feature_values == [latest.features[name] for name in schema.names]
                assert len(latest.feature_values) == 209
                assert all(value is None or math.isfinite(value) for value in latest.feature_values)
                assert 0 <= latest.features["cpu_usage_percent.latest"] <= 100
                assert 0 <= latest.features["memory_usage_percent.latest"] <= 100
                assert latest.features["cpu_usage_percent.linear_trend"] is not None
                for metric in latest.quality["metrics"].values():
                    if metric["count"]:
                        assert (
                            latest.window_start.timestamp()
                            < metric["first_timestamp"]
                            <= metric["last_timestamp"]
                            <= latest.window_end.timestamp()
                        )
                if not host.service_job:
                    assert all(
                        latest.features[f"{metric}.latest"] is None for metric in APP_METRICS
                    )
                summaries.append(
                    {
                        "host_id": host.id,
                        "service_job": host.service_job,
                        "latest_window_end": latest.window_end.isoformat(),
                        "recent_window_ends": [row.window_end.isoformat() for row in rows],
                        "cpu_latest": latest.features["cpu_usage_percent.latest"],
                        "cpu_slope_per_second": latest.features["cpu_usage_percent.linear_trend"],
                        "memory_latest": latest.features["memory_usage_percent.latest"],
                        "memory_slope_per_second": latest.features[
                            "memory_usage_percent.linear_trend"
                        ],
                        "request_rate": latest.features["request_rate.latest"],
                        "latency_p95_seconds": latest.features["latency_p95_seconds.latest"],
                        "cpu_sample_count": latest.quality["metrics"]["cpu_usage_percent"]["count"],
                        "query_errors": latest.quality["query_errors"],
                    }
                )
            empty_tables = {}
            for table in (
                "failure_events",
                "predictions",
                "incidents",
                "alerts",
                "chaos_experiments",
                "model_versions",
                "evaluation_runs",
                "remediation_proposals",
            ):
                count = await session.scalar(text(f"SELECT count(*) FROM {table}"))
                if table not in {"failure_events", "chaos_experiments"}:
                    assert count == 0
                empty_tables[table] = count
        record = {
            "verified_at": now.isoformat(),
            "start_windows": start_count,
            "end_windows": end_count,
            "observation_seconds": 75,
            "hypertable_verified": True,
            "schema_version": schema.version,
            "schema_hash": schema.digest,
            "feature_count": len(schema.names),
            "hosts": summaries,
            "operational_table_counts": empty_tables,
        }
        path = ROOT / "docs" / "feature-validation.json"
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=create_event_loop)
