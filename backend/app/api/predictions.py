"""Bounded read-only access to actual persisted inference records."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Alert, Incident, ModelVersion, Prediction

router = APIRouter(tags=["predictions and incidents"])


@router.get("/inference/status")
async def inference_status(request: Request) -> dict[str, Any]:
    async with async_sessionmaker(request.app.state.engine)() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        latest = await session.scalar(
            select(Prediction).order_by(Prediction.predicted_at.desc()).limit(1)
        )
        model = (
            await session.get(ModelVersion, latest.model_version_id)
            if latest
            else await session.scalar(
                select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(1)
            )
        )
        age = (now - latest.predicted_at).total_seconds() if latest else None
        return {
            "status": "recent_scores_available"
            if age is not None and 0 <= age <= 90
            else "stale"
            if latest
            else "no_predictions",
            "as_of": now,
            "model_version": model.version if model else None,
            "last_prediction_age_seconds": age,
            "latest_prediction": {
                "id": latest.id,
                "host": latest.host_id,
                "probability": latest.probability,
                "anomaly_score": latest.anomaly_score,
                "horizon_seconds": latest.horizon_seconds,
                "feature_window_end": latest.feature_window_end,
                "predicted_at": latest.predicted_at,
                "forecast_valid_until": latest.explanation.get("forecast_valid_until"),
            }
            if latest
            else None,
            "model_evaluation": model.training_metadata.get("evaluation", {}).get("test")
            if model
            else None,
            "limitations": model.training_metadata.get("limitations", [])
            if model
            else ["No registered model"],
        }


async def records(request: Request, model: Any, limit: int) -> list[dict[str, Any]]:
    async with async_sessionmaker(request.app.state.engine)() as session:
        rows: list[Any] = list(
            (
                await session.scalars(
                    select(model).order_by(model.created_at.desc(), model.id.desc()).limit(limit)
                )
            ).all()
        )
        return [
            {column.name: getattr(row, column.name) for column in model.__table__.columns}
            for row in rows
        ]


@router.get("/predictions")
async def predictions(
    request: Request, limit: int = Query(default=50, ge=1, le=100)
) -> list[dict[str, Any]]:
    return await records(request, Prediction, limit)


@router.get("/incidents")
async def incidents(
    request: Request, limit: int = Query(default=50, ge=1, le=100)
) -> list[dict[str, Any]]:
    return await records(request, Incident, limit)


@router.get("/alerts")
async def alerts(
    request: Request, limit: int = Query(default=50, ge=1, le=100)
) -> list[dict[str, Any]]:
    return await records(request, Alert, limit)
