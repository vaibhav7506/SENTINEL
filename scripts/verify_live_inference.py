# ruff: noqa: E402
"""Real live chaos and actual prediction/alert timestamps; no forced detections."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_chaos_campaign import main as campaign
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.models import Alert, Incident, ModelVersion, Prediction, RemediationProposal


async def main() -> None:
    started = datetime.now(UTC)
    await campaign(ROOT / "docs/phase-6-live-campaign.json", baseline_consecutive_breaches=3)
    await asyncio.sleep(30)
    # Bound individual waits; ensure one real post-recovery minute is scored.
    await asyncio.sleep(35)
    evidence = json.loads((ROOT / "docs/phase-6-live-campaign.json").read_text())
    settings = Settings(_env_file=ROOT / ".env")
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session:
            model = await session.scalar(
                select(ModelVersion).where(ModelVersion.version == "20260926T204848-mlp-1bcd8acd")
            )
            assert model is not None
            predictions = (
                await session.scalars(
                    select(Prediction)
                    .where(
                        Prediction.model_version_id == model.id,
                        Prediction.predicted_at >= started - timedelta(seconds=600),
                    )
                    .order_by(Prediction.predicted_at)
                )
            ).all()
            alerts = (
                await session.scalars(
                    select(Alert)
                    .join(Incident)
                    .where(
                        Incident.model_version_id == model.id,
                        Alert.created_at >= started - timedelta(seconds=600),
                    )
                )
            ).all()
            assert predictions and all(
                p.anomaly_score is not None and p.horizon_seconds == 600 for p in predictions
            )
            identities = {
                (p.host_id, p.model_version_id, p.feature_window_end) for p in predictions
            }
            assert len(identities) == len(predictions)
            outcomes = []
            for result in evidence["results"]:
                event = result["failure_event"]
                onset = datetime.fromisoformat(event["observed_at"])
                before = [
                    p
                    for p in predictions
                    if p.host_id == event["host_id"]
                    and p.feature_window_end
                    < onset
                    <= p.feature_window_end + timedelta(seconds=p.horizon_seconds)
                    and p.predicted_at < onset
                ]
                warnings = [p for p in before if p.probability >= model.threshold]
                delivered = [
                    a
                    for a in alerts
                    if a.status == "delivered"
                    and a.delivered_at is not None
                    and a.delivered_at < onset
                    and datetime.fromisoformat(a.payload["feature_window_end"])
                    < onset
                    <= datetime.fromisoformat(a.payload["forecast_valid_until"])
                ]
                first = min(delivered, key=lambda alert: alert.delivered_at) if delivered else None
                lead = (onset - first.delivered_at).total_seconds() if first else None
                outcomes.append(
                    {
                        "failure_event_id": event["id"],
                        "failure_type": event["failure_type"],
                        "actual_onset": event["observed_at"],
                        "confirmed_at": event["confirmed_at"],
                        "recovered_at": event["recovered_at"],
                        "actual_pre_failure_predictions": len(before),
                        "threshold_crossings_before_failure": len(warnings),
                        "pre_failure_probability_range": [
                            min(p.probability for p in before),
                            max(p.probability for p in before),
                        ]
                        if before
                        else None,
                        "earliest_pre_failure_alert_id": str(first.id) if first else None,
                        "local_alert_lead_seconds": lead,
                        "useful_warning_at_least_60_seconds": lead is not None and lead >= 60,
                        "outcome": "Local alert preceded actual degradation"
                        if first
                        else "No pre-failure alert; no predictive-success claim",
                    }
                )
            record = {
                "verified_at": datetime.now(UTC).isoformat(),
                "started_at": started.isoformat(),
                "model_version": model.version,
                "unchanged_validation_threshold": model.threshold,
                "live_predictions_checked": len(predictions),
                "unique_feature_windows": True,
                "horizon_seconds": 600,
                "separate_anomaly_scores_verified": True,
                "delivery_scope": "local test receiver only",
                "outcomes": outcomes,
                "actual_alerts": [
                    {
                        "id": str(a.id),
                        "status": a.status,
                        "channel": a.channel,
                        "delivered_at": a.delivered_at.isoformat() if a.delivered_at else None,
                    }
                    for a in alerts
                ],
                "remediation_proposals": len(
                    (await session.scalars(select(RemediationProposal))).all()
                ),
                "model_limitations": model.training_metadata["limitations"],
            }
    finally:
        await engine.dispose()
    (ROOT / "docs/phase-6-live-validation.json").write_text(
        json.dumps(record, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=create_event_loop)
