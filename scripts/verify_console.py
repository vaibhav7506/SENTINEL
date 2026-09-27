# ruff: noqa: E402
"""Compare local console views with actual database records; no writes or fault injection."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.models import (
    Alert,
    ChaosExperiment,
    EvaluationRun,
    FailureEvent,
    Host,
    Incident,
    ModelVersion,
    Prediction,
    RemediationProposal,
)


async def verify() -> dict:
    settings = Settings(_env_file=ROOT / ".env")
    settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    engine = create_database_engine(settings)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:5173/api", timeout=15) as client:
        response = await client.get("/console/snapshot")
        response.raise_for_status()
        data = response.json()
        tables = {
            "hosts": Host,
            "predictions": Prediction,
            "incidents": Incident,
            "alerts": Alert,
            "experiments": ChaosExperiment,
            "models": ModelVersion,
            "evaluations": EvaluationRun,
            "proposals": RemediationProposal,
            "failure_events": FailureEvent,
        }
        try:
            async with async_sessionmaker(engine)() as session:
                for name, model in tables.items():
                    count = await session.scalar(select(func.count()).select_from(model))
                    # Worker can append predictions between the snapshot and database read.
                    if name == "predictions":
                        assert count >= data["totals"][name]
                    else:
                        assert count == data["totals"][name], name
                for shown in data["evaluations"]:
                    actual = await session.get(EvaluationRun, shown["id"])
                    assert actual and actual.metrics == shown["metrics"]
                    assert actual.split_definition == shown["split_definition"]
                for shown in data["predictions"]:
                    actual = await session.get(Prediction, shown["id"])
                    assert actual and actual.probability == shown["probability"]
                    assert actual.anomaly_score == shown["anomaly_score"]
                    assert actual.explanation == shown["explanation"]
                for shown in data["models"]:
                    actual = await session.get(ModelVersion, shown["id"])
                    assert actual and actual.training_metadata == shown["training_metadata"]
                    assert actual.threshold == shown["threshold"]
                assert data["overview"]["healthy_hosts"] == sum(
                    h["status"] == "healthy" for h in data["hosts"]
                )
                assert data["overview"]["high_risk_hosts"] == sum(
                    h["high_risk"] for h in data["hosts"]
                )
                assert data["governance"]["human_approval_required"] is True
                assert data["governance"]["enabled"] is settings.runbookos_enabled
        finally:
            await engine.dispose()
        host_results = []
        for host in data["hosts"]:
            response = await client.get("/console/hosts/" + host["id"])
            response.raise_for_status()
            detail = response.json()
            assert detail["host_id"] == host["id"]
            assert all(p["host_id"] == host["id"] for p in detail["predictions"])
            host_results.append(
                {
                    "host": host["id"],
                    "observed_status": host["status"],
                    "prediction_fresh": host["prediction_fresh"],
                    "raw_telemetry_status": detail["telemetry"]["status"],
                    "raw_metric_series": len(detail["telemetry"]["series"]),
                }
            )
        assert (await client.get("/console/hosts/nonexistent-sentinel-host")).status_code == 404
        schema = (await client.get("/openapi.json")).json()
        assert all(
            set(methods) == {"get"}
            for path, methods in schema["paths"].items()
            if path.startswith("/console/")
        )
        held_out = data["evaluations"][0]["metrics"]["test"] if data["evaluations"] else None
        return {
            "verified_at": datetime.now(UTC).isoformat(),
            "status": "passed",
            "totals": data["totals"],
            "hosts": host_results,
            "exact_prediction_values_verified": len(data["predictions"]),
            "evaluation_runs_verified": len(data["evaluations"]),
            "held_out_test": {
                k: held_out[k] for k in ("precision", "recall", "f1", "pr_auc", "confusion_matrix")
            }
            if held_out
            else None,
            "experiment_coverage": [
                {
                    "id": e["id"],
                    "coverage": e["coverage"],
                    "eligible_scores": e["eligible_scores"],
                    "alert_lead_seconds": e["alert_lead_seconds"],
                }
                for e in data["experiments"]
            ],
            "console_routes_get_only": True,
            "governance": data["governance"],
        }


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        result = runner.run(verify())
    (ROOT / "docs/phase-8-live-console.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
