"""Read-only verification of fresh persisted scores against deployed frozen artifacts."""

import asyncio
import json
import math
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.models import FeatureWindow, ModelVersion, Prediction
from inference.scoring import ROOT, Scorer


async def verify() -> dict[str, object]:
    settings = Settings()
    scorer = Scorer(ROOT / settings.inference_model_path)
    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session:
            model = await session.scalar(
                select(ModelVersion).where(ModelVersion.version == scorer.metadata["version"])
            )
            if model is None or model.training_metadata != scorer.metadata:
                raise ValueError("Registered model differs from verified frozen artifacts")
            prediction = await session.scalar(
                select(Prediction)
                .where(Prediction.model_version_id == model.id)
                .order_by(Prediction.predicted_at.desc())
                .limit(1)
            )
            if prediction is None:
                raise ValueError("No actual prediction is available")
            age = (datetime.now(UTC) - prediction.predicted_at).total_seconds()
            if not 0 <= age <= 120:
                raise ValueError("Latest actual prediction is stale")
            window = await session.scalar(
                select(FeatureWindow).where(
                    FeatureWindow.host_id == prediction.host_id,
                    FeatureWindow.window_end == prediction.feature_window_end,
                )
            )
            if window is None or window.feature_names != scorer.names:
                raise ValueError("Prediction feature evidence is absent or incompatible")
            actual = scorer.score(window.feature_values, explain=False)
            if not math.isclose(
                actual["failure_probability"], prediction.probability, rel_tol=1e-5, abs_tol=1e-7
            ):
                raise ValueError("Failure score does not reproduce")
            if prediction.anomaly_score is None or not math.isclose(
                actual["anomaly_score"], prediction.anomaly_score, rel_tol=1e-5, abs_tol=1e-7
            ):
                raise ValueError("Separate anomaly score does not reproduce")
            return {
                "status": "passed",
                "model_loaded": True,
                "model_version": model.version,
                "unchanged_threshold": model.threshold,
                "prediction_id": str(prediction.id),
                "host_id": prediction.host_id,
                "prediction_age_seconds": age,
                "horizon_seconds": prediction.horizon_seconds,
                "failure_probability": prediction.probability,
                "anomaly_score": prediction.anomaly_score,
                "frozen_scores_reproduced": True,
                "model_validation": "Held-out recall/F1 remain zero; no validation claim",
            }
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(verify(), loop_factory=create_event_loop), indent=2))
