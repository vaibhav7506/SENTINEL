# ruff: noqa: E402
"""Verify the generated artifact against actual persisted features and event/probe evidence."""

import asyncio
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.models import (
    ChaosExperiment,
    FailureEvent,
    FeatureWindow,
    ServiceObservation,
)
from training.labels import Event, Probe, label_window


async def main() -> None:
    summary = json.loads((ROOT / "docs" / "phase-4-dataset.json").read_text(encoding="utf-8"))
    path = Path(summary["path"])
    assert path.resolve().is_relative_to(ROOT / "artifacts" / "datasets")
    data = (path / "samples.jsonl").read_bytes()
    assert hashlib.sha256(data).hexdigest() == summary["dataset_sha256"]
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    assert len(rows) == summary["samples"] > 0
    assert dict(Counter(str(row["label"]) for row in rows)) == summary["label_distribution"]
    assert {row["label"] for row in rows} == {0, 1}
    settings = Settings(_env_file=ROOT / ".env")
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    engine = create_database_engine(settings)
    as_of = datetime.fromisoformat(summary["as_of"])
    criteria = summary["negative_label_criterion"]
    try:
        async with async_sessionmaker(engine)() as session:
            windows = {
                (row.host_id, row.window_end.isoformat()): row
                for row in (await session.scalars(select(FeatureWindow))).all()
            }
            events = [
                Event(
                    str(row.id),
                    row.host_id,
                    str(row.experiment_id) if row.experiment_id else None,
                    row.failure_type,
                    row.observed_at,
                    row.confirmed_at,
                    row.recovered_at,
                )
                for row in (await session.scalars(select(FailureEvent))).all()
                if row.confirmed_at is not None
            ]
            observations = list((await session.scalars(select(ServiceObservation))).all())
            experiments = list((await session.scalars(select(ChaosExperiment))).all())
            assert len(experiments) >= 2
            assert len(events) >= 2 and all(event.recovered is not None for event in events)
            for row in rows:
                window = windows[row["host_id"], row["window_end"]]
                assert window.window_end <= as_of
                assert row["feature_values"] == window.feature_values
                assert window.feature_names == summary["feature_schema"]["names"]
                assert window.schema_hash == summary["feature_schema"]["hash"]
                assert len(row["feature_values"]) == 209
                assert all(value is None or math.isfinite(value) for value in row["feature_values"])
                probes = [
                    Probe(
                        probe.observed_at,
                        probe.status_code == 200
                        and probe.latency_seconds <= criteria["latency_slo_seconds"],
                        criteria,
                    )
                    for probe in observations
                    if probe.host_id == row["host_id"]
                ]
                result = label_window(
                    row["host_id"],
                    window.window_end,
                    summary["horizon_seconds"],
                    summary["recovery_exclusion_seconds"],
                    events,
                    probes,
                    criteria,
                    as_of,
                )
                assert result.value == row["label"] and result.event_id == row["failure_event_id"]
            for identifier in {identifier for row in rows for identifier in row["experiment_ids"]}:
                assert (
                    len({row["split"] for row in rows if identifier in row["experiment_ids"]}) == 1
                )
            for train in (row for row in rows if row["split"] == "train"):
                a = datetime.fromisoformat(train["window_start"])
                b = datetime.fromisoformat(train["window_end"]) + timedelta(
                    seconds=summary["horizon_seconds"]
                )
                for test in (
                    row
                    for row in rows
                    if row["split"] == "test" and row["host_id"] == train["host_id"]
                ):
                    c = datetime.fromisoformat(test["window_start"])
                    d = datetime.fromisoformat(test["window_end"]) + timedelta(
                        seconds=summary["horizon_seconds"]
                    )
                    assert b < c or d < a
        values = dict(
            line.split("=", 1)
            for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.startswith("#")
        )
        async with httpx.AsyncClient(timeout=5) as client:
            # Check real gates without causing another experiment.
            api = "http://127.0.0.1:" + values.get("API_PORT", "8000")
            demo = "http://127.0.0.1:" + values.get("DEMO_PORT", "8001")
            assert (
                await client.post(api + "/chaos/experiments", json={"failure_type": "http_error"})
            ).status_code == 401
            assert (
                await client.post(
                    api + "/chaos/experiments",
                    json={"failure_type": "http_error", "namespace": "production"},
                )
            ).status_code == 422
            assert (
                await client.post(
                    api + "/chaos/experiments",
                    json={"failure_type": "http_error", "command": "invalid"},
                )
            ).status_code == 422
            response = await client.get(
                demo + "/control/chaos",
                headers={
                    "Authorization": "Bearer " + settings.chaos_control_token.get_secret_value()
                },
            )
            response.raise_for_status()
            assert response.json()["active"] is False
            assert (await client.get(demo + "/work?units=1")).status_code == 200
        result = {
            "dataset_path": str(path),
            "samples": len(rows),
            "labels_recomputed_from_database": True,
            "sha256_verified": True,
            "feature_vectors_match_database": True,
            "experiment_groups_disjoint": True,
            "overlapping_train_test_intervals": False,
            "no_active_fault": True,
            "live_unauthorized_and_unsupported_requests_rejected": True,
        }
        (ROOT / "docs" / "phase-4-validation.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=create_event_loop)
