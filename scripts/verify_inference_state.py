# ruff: noqa: E402
"""Read-only live inference verification; never inject faults or deliver alerts."""

import argparse
import asyncio
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.models import (
    Alert,
    ChaosExperiment,
    FailureEvent,
    FeatureWindow,
    Prediction,
    RemediationProposal,
)


async def check_receiver(client: httpx.AsyncClient) -> list[str]:
    """Replay existing local receipts; a restart must retain their deduplication."""
    receipt_path = ROOT / "docs" / "phase-6-webhook-receipts.jsonl"
    before = receipt_path.read_bytes()
    receipts = [json.loads(line) for line in before.decode("utf-8").splitlines()]
    checked = []
    for channel in ("generic", "slack"):
        receipt = next(
            r
            for r in receipts
            if r["channel"] == channel and r["kind"] == "historical_contract_verification"
        )
        response = await client.post(
            f"http://127.0.0.1:8091/{channel}",
            json=receipt["payload"],
            headers={
                "Idempotency-Key": receipt["idempotency_key"],
                "X-Sentinel-Verification": "historical_contract_verification",
            },
        )
        response.raise_for_status()
        assert response.json() == {"accepted": True, "duplicate_suppressed": True}
        checked.append(channel)
    assert receipt_path.read_bytes() == before
    return checked


async def verify(output: Path) -> None:
    output = output.resolve()
    if not output.is_relative_to(ROOT / "docs"):
        raise ValueError("Verification evidence must stay inside workspace docs")
    settings = Settings(_env_file=ROOT / ".env")  # type: ignore[call-arg]
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    engine = create_database_engine(settings)
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            receiver_channels = await check_receiver(client)
            response = await client.get("http://127.0.0.1:8000/inference/status")
            response.raise_for_status()
            status = response.json()
            if status["status"] != "recent_scores_available":
                async with async_sessionmaker(engine)() as session:
                    window = await session.scalar(
                        select(FeatureWindow).order_by(FeatureWindow.window_end.desc()).limit(1)
                    )
                    print(
                        json.dumps(
                            {
                                "status": "awaiting_fresh_eligible_window",
                                "api_status": status["status"],
                                "latest_window_end": window.window_end.isoformat()
                                if window
                                else None,
                                "cpu_history_seconds": window.quality["metrics"][
                                    "cpu_usage_percent"
                                ]["coverage_seconds"]
                                if window
                                else None,
                                "required_cpu_history_seconds": 0.8
                                * settings.feature_window_seconds,
                            },
                            indent=2,
                        )
                    )
                raise SystemExit(2)

            # Heavy numerical imports only follow a real, fresh stored prediction.
            from inference.scoring import Scorer
            from inference.worker import eligible

            scorer = Scorer(ROOT / settings.inference_model_path)
            assert status["model_version"] == scorer.metadata["version"]
            checked_routes = []
            for route in ("predictions", "incidents", "alerts"):
                listing = await client.get(f"http://127.0.0.1:8000/{route}?limit=1")
                listing.raise_for_status()
                assert isinstance(listing.json(), list) and len(listing.json()) <= 1
                for invalid in (0, 101):
                    invalid_response = await client.get(
                        f"http://127.0.0.1:8000/{route}?limit={invalid}"
                    )
                    assert invalid_response.status_code == 422
                checked_routes.append(route)

            async with async_sessionmaker(engine)() as session:
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(now, datetime)
                prediction = await session.scalar(
                    select(Prediction).order_by(Prediction.predicted_at.desc()).limit(1)
                )
                assert prediction is not None
                assert 0 <= (now - prediction.predicted_at).total_seconds() <= 90
                window = await session.scalar(
                    select(FeatureWindow).where(
                        FeatureWindow.host_id == prediction.host_id,
                        FeatureWindow.window_end == prediction.feature_window_end,
                    )
                )
                assert window is not None
                assert eligible(window, scorer.metadata, now, settings.inference_max_age_seconds)
                assert window.window_end <= prediction.predicted_at
                assert prediction.horizon_seconds == scorer.metadata["horizon_seconds"]
                valid_until = window.window_end + timedelta(seconds=prediction.horizon_seconds)
                assert (
                    datetime.fromisoformat(prediction.explanation["forecast_valid_until"])
                    == valid_until
                )
                actual = scorer.score(window.feature_values, False)
                assert math.isclose(
                    prediction.probability,
                    actual["failure_probability"],
                    rel_tol=1e-5,
                    abs_tol=1e-7,
                )
                assert prediction.anomaly_score is not None
                assert math.isclose(
                    prediction.anomaly_score,
                    actual["anomaly_score"],
                    rel_tol=1e-5,
                    abs_tol=1e-7,
                )
                duplicate_groups = await session.scalar(
                    text(
                        "SELECT count(*) FROM (SELECT host_id, model_version_id, "
                        "feature_window_end "
                        "FROM predictions GROUP BY host_id, model_version_id, feature_window_end "
                        "HAVING count(*) > 1) duplicated"
                    )
                )
                assert duplicate_groups == 0
                active_experiments = await session.scalar(
                    select(func.count())
                    .select_from(ChaosExperiment)
                    .where(ChaosExperiment.status.in_(["running", "pending"]))
                )
                unrecovered_events = await session.scalar(
                    select(func.count())
                    .select_from(FailureEvent)
                    .where(FailureEvent.recovered_at.is_(None))
                )
                remediation_count = await session.scalar(
                    select(func.count()).select_from(RemediationProposal)
                )
                assert (
                    active_experiments == 0 and unrecovered_events == 0 and remediation_count == 0
                )
                counts = {
                    "predictions": await session.scalar(
                        select(func.count()).select_from(Prediction)
                    ),
                    "alerts": await session.scalar(select(func.count()).select_from(Alert)),
                    "duplicate_prediction_groups": duplicate_groups,
                    "active_experiments": active_experiments,
                    "unrecovered_failure_events": unrecovered_events,
                    "remediation_proposals": remediation_count,
                }

            metrics_response = await client.get(
                f"http://127.0.0.1:{settings.inference_metrics_port}/metrics"
            )
            metrics_response.raise_for_status()
            metrics = {
                sample.name: sample.value
                for family in text_string_to_metric_families(metrics_response.text)
                for sample in family.samples
                if sample.name.startswith("sentinel_")
            }
            assert (
                0
                <= datetime.now(UTC).timestamp()
                - metrics["sentinel_inference_heartbeat_timestamp_seconds"]
                <= 30
            )
            assert metrics["sentinel_predictions_total"] >= 1
            assert metrics["sentinel_inference_errors_total"] == 0
            scrape = await client.get(
                str(settings.prometheus_url).rstrip("/") + "/api/v1/query",
                params={"query": 'up{job="sentinel-inference"}'},
            )
            scrape.raise_for_status()
            scrape_result = scrape.json()["data"]["result"]
            assert len(scrape_result) == 1 and scrape_result[0]["value"][1] == "1"
            receiver = await client.get("http://127.0.0.1:8091/openapi.json")
            receiver.raise_for_status()
            assert receiver.json()["info"]["title"] == "Sentinel local alert test receiver"

            record: dict[str, Any] = {
                "verified_at": datetime.now(UTC).isoformat(),
                "status": "passed",
                "scope": (
                    "Restored live state and deduplicated existing local receipt replays; "
                    "no new chaos, operational alerts, or external messages"
                ),
                "model_version": status["model_version"],
                "unchanged_threshold": scorer.metadata["threshold"],
                "latest_prediction": {
                    "id": str(prediction.id),
                    "host": prediction.host_id,
                    "predicted_at": prediction.predicted_at.isoformat(),
                    "feature_window_end": prediction.feature_window_end.isoformat(),
                    "forecast_valid_until": valid_until.isoformat(),
                    "failure_probability": prediction.probability,
                    "anomaly_score": prediction.anomaly_score,
                    "horizon_seconds": prediction.horizon_seconds,
                },
                "score_reproduction": "Both separate scores match the frozen artifacts",
                "feature_quality_and_freshness_verified": True,
                "forecast_validity_verified": True,
                "bounded_list_routes_verified": checked_routes,
                "database_counts": counts,
                "inference_metrics": metrics,
                "prometheus_inference_scrape_up": True,
                "local_receiver_available": True,
                "receiver_restart_deduplication_verified": receiver_channels,
                "predictive_success_claim": False,
            }
            output.write_text(
                json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
            )
            print(json.dumps(record, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/phase-6-restored-live.json")
    asyncio.run(verify(parser.parse_args().output), loop_factory=create_event_loop)
