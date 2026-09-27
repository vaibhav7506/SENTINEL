# ruff: noqa: E402
"""Historical real-model SHAP and loopback HTTP checks; not a live prediction."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from app.core.event_loop import create_event_loop
from inference.alerts import deliver
from inference.scoring import Scorer
from inference.summaries import summarize
from inference.worker import alert_timing


async def main() -> None:
    scorer = Scorer(ROOT / "artifacts/models/20260926T204848-mlp-1bcd8acd")
    dataset = Path(scorer.metadata["dataset_reference"])
    rows = [json.loads(line) for line in (dataset / "samples.jsonl").read_text().splitlines()]
    saved = [
        json.loads(line)
        for line in (ROOT / "artifacts/models" / scorer.metadata["version"] / "scores.jsonl")
        .read_text()
        .splitlines()
    ]
    original = next(row for row in saved if row["warning"] and row["label"] == 1)
    row = next(
        r
        for r in rows
        if r["window_end"] == original["window_end"] and r["host_id"] == original["host_id"]
    )
    result = scorer.score(row["feature_values"])
    valid_until = datetime.fromisoformat(row["window_end"]) + timedelta(seconds=600)
    summary = await summarize(
        [
            "Historical contract verification: this is not a live model warning.",
            (
                f"Actual historical probability {result['failure_probability']:.6f}; "
                f"separate anomaly {result['anomaly_score']:.6f}."
            ),
            "Poor held-out model; known degradation occurred in the past.",
            "Actual associated features: " + json.dumps(result["top_drivers"]),
        ]
    )
    identifier = "historical-contract-" + uuid4().hex
    payload = {
        "kind": "historical_contract_verification",
        "incident_id": identifier,
        "host": row["host_id"],
        "service": "demo-service",
        **result,
        "horizon_seconds": 600,
        "forecast_valid_until": valid_until.isoformat(),
        "feature_window_end": row["window_end"],
        "summary": summary,
        "dashboard_link": "http://127.0.0.1:5173",
        "model_validation_warning": (
            "Historical contract verification; not an operational prediction"
        ),
    }
    records = []
    async with httpx.AsyncClient(
        headers={"X-Sentinel-Verification": "historical_contract_verification"},
        trust_env=False,
    ) as client:
        for channel in ("generic", "slack"):
            key = identifier + "-" + channel
            status, error = await deliver(
                client, "http://127.0.0.1:8091/" + channel, channel, key, payload
            )
            assert status == "delivered", error
            acknowledged = datetime.now(UTC)
            # Identical stable ID must not create another receiver receipt.
            duplicate, _ = await deliver(
                client, "http://127.0.0.1:8091/" + channel, channel, key, payload
            )
            assert duplicate == "delivered"
            records.append(
                {
                    "channel": channel,
                    "scope": "loopback receiver; not external Slack",
                    "idempotency_key": key,
                    "acknowledged_at": acknowledged.isoformat(),
                    "timing_against_historical_failure": alert_timing(
                        acknowledged,
                        datetime.fromisoformat(row["failure_onset"]),
                        valid_until,
                    ),
                }
            )
    receipts = [
        json.loads(line)
        for line in (ROOT / "docs/phase-6-webhook-receipts.jsonl").read_text().splitlines()
    ]
    assert all(
        sum(receipt["idempotency_key"] == item["idempotency_key"] for receipt in receipts) == 1
        for item in records
    )
    record = {
        "verified_at": datetime.now(UTC).isoformat(),
        "kind": "historical_contract_verification",
        "is_live_prefailure_prediction": False,
        "source_window_end": row["window_end"],
        "source_failure_onset": row["failure_onset"],
        "model_version": scorer.metadata["version"],
        "dataset_sha256": scorer.metadata["dataset_sha256"],
        "score_and_shap": result,
        "summary": summary,
        "delivery_checks": records,
        "duplicate_receiver_receipts_suppressed": True,
    }
    (ROOT / "docs/phase-6-explanation-delivery.json").write_text(
        json.dumps(record, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(
        "PASS real historical SHAP, factual fallback, local generic/Slack payload HTTP "
        "and duplicate suppression; no live prediction claim"
    )


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=create_event_loop)
