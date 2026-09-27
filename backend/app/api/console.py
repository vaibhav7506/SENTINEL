"""Read-only console views. Counts and timing use persisted facts, not demo fixtures."""

import asyncio
import json
import math
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    ServiceObservation,
)
from app.services.prometheus import create_prometheus_client

router = APIRouter(prefix="/console", tags=["console"])
LIMIT = 100


def record(row: Any) -> dict[str, Any]:
    result = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    # Delivery destinations and potentially sensitive transport payloads aren't UI data.
    if isinstance(row, Alert):
        result.pop("payload", None)
    if isinstance(row, RemediationProposal):
        result.pop("parameters", None)
    return result


def host_status(now: datetime, observation: ServiceObservation | None, active_failure: bool) -> str:
    if active_failure:
        return "degraded"
    if observation is None or not 0 <= (now - observation.observed_at).total_seconds() <= 90:
        return "unknown"
    return "degraded" if observation.breached else "healthy"


def useful_lead(
    prediction: Prediction, onset: datetime, delivered: datetime | None = None
) -> float | None:
    """Only an advance warning whose anchored forecast covers onset earns lead time."""
    warning = delivered or prediction.predicted_at
    if warning < prediction.predicted_at:
        return None
    lead = (onset - warning).total_seconds()
    valid_until = prediction.feature_window_end + timedelta(seconds=prediction.horizon_seconds)
    return lead if lead >= 60 and prediction.feature_window_end <= onset <= valid_until else None


async def recent(session: AsyncSession, model: Any, **filters: Any) -> list[Any]:
    query = select(model).filter_by(**filters).order_by(model.created_at.desc()).limit(LIMIT)
    return list((await session.scalars(query)).all())


async def incident_view(session: AsyncSession, incident: Incident) -> dict[str, Any]:
    prediction = (
        await session.get(Prediction, incident.prediction_id) if incident.prediction_id else None
    )
    event = (
        await session.get(FailureEvent, incident.failure_event_id)
        if incident.failure_event_id
        else None
    )
    return {
        **record(incident),
        "prediction": record(prediction) if prediction else None,
        "failure_event": record(event) if event else None,
        "alerts": [record(a) for a in await recent(session, Alert, incident_id=incident.id)],
        "proposals": [
            record(p) for p in await recent(session, RemediationProposal, incident_id=incident.id)
        ],
    }


async def hosts_view(session: AsyncSession, now: datetime) -> list[dict[str, Any]]:
    hosts = (await session.scalars(select(Host).order_by(Host.id).limit(1000))).all()
    host_ids = [host.id for host in hosts]
    predictions = {
        row.host_id: row
        for row in (
            await session.scalars(
                select(Prediction)
                .where(Prediction.host_id.in_(host_ids))
                .distinct(Prediction.host_id)
                .order_by(Prediction.host_id, Prediction.predicted_at.desc())
            )
        ).all()
    }
    observations = {
        row.host_id: row
        for row in (
            await session.scalars(
                select(ServiceObservation)
                .where(ServiceObservation.host_id.in_(host_ids))
                .distinct(ServiceObservation.host_id)
                .order_by(ServiceObservation.host_id, ServiceObservation.observed_at.desc())
            )
        ).all()
    }
    degraded = set(
        (
            await session.scalars(
                select(FailureEvent.host_id)
                .where(FailureEvent.recovered_at.is_(None), FailureEvent.host_id.in_(host_ids))
                .distinct()
            )
        ).all()
    )
    thresholds = dict(
        (
            await session.execute(
                select(ModelVersion.id, ModelVersion.threshold).where(
                    ModelVersion.id.in_([p.model_version_id for p in predictions.values()])
                )
            )
        ).all()
    )
    output = []
    for host in hosts:
        prediction = predictions.get(host.id)
        age = (now - prediction.predicted_at).total_seconds() if prediction else None
        fresh = age is not None and 0 <= age <= 90
        observation = observations.get(host.id)
        output.append(
            {
                **record(host),
                "status": host_status(now, observation, host.id in degraded),
                "observation": record(observation) if observation else None,
                "latest_prediction": record(prediction) if prediction else None,
                "prediction_age_seconds": age,
                "prediction_fresh": fresh,
                "high_risk": bool(
                    fresh
                    and prediction
                    and prediction.probability >= thresholds[prediction.model_version_id]
                ),
                "threshold": thresholds.get(prediction.model_version_id) if prediction else None,
            }
        )
    return output


async def host_counts(session: AsyncSession, now: datetime) -> tuple[int, int]:
    """Aggregate across every host without loading their histories into Python."""
    observations = (
        select(
            ServiceObservation.host_id, ServiceObservation.observed_at, ServiceObservation.breached
        )
        .distinct(ServiceObservation.host_id)
        .order_by(ServiceObservation.host_id, ServiceObservation.observed_at.desc())
        .subquery()
    )
    predictions = (
        select(
            Prediction.host_id,
            Prediction.predicted_at,
            Prediction.probability,
            Prediction.model_version_id,
        )
        .distinct(Prediction.host_id)
        .order_by(Prediction.host_id, Prediction.predicted_at.desc())
        .subquery()
    )
    degraded = (
        select(FailureEvent.host_id)
        .where(FailureEvent.recovered_at.is_(None))
        .distinct()
        .subquery()
    )
    healthy = (
        (observations.c.observed_at >= now - timedelta(seconds=90))
        & (observations.c.observed_at <= now)
        & (~observations.c.breached)
        & degraded.c.host_id.is_(None)
    )
    risky = (
        (predictions.c.predicted_at >= now - timedelta(seconds=90))
        & (predictions.c.predicted_at <= now)
        & (predictions.c.probability >= ModelVersion.threshold)
    )
    result = (
        await session.execute(
            select(
                func.coalesce(func.sum(case((healthy, 1), else_=0)), 0),
                func.coalesce(func.sum(case((risky, 1), else_=0)), 0),
            )
            .select_from(Host)
            .outerjoin(observations, observations.c.host_id == Host.id)
            .outerjoin(predictions, predictions.c.host_id == Host.id)
            .outerjoin(ModelVersion, ModelVersion.id == predictions.c.model_version_id)
            .outerjoin(degraded, degraded.c.host_id == Host.id)
        )
    ).one()
    return int(result[0]), int(result[1])


async def experiment_view(session: AsyncSession, experiment: ChaosExperiment) -> dict[str, Any]:
    event = await session.scalar(
        select(FailureEvent)
        .where(FailureEvent.experiment_id == experiment.id)
        .order_by(FailureEvent.observed_at)
        .limit(1)
    )
    result = {**record(experiment), "failure_event": record(event) if event else None}
    if not event:
        return {
            **result,
            "coverage": "No observed failure",
            "eligible_scores": 0,
            "warning_lead_seconds": None,
            "alert_lead_seconds": None,
        }
    # Query all eligible windows for this event; UI list limits never imply a missed warning.
    candidates = (
        await session.execute(
            select(Prediction, ModelVersion.threshold)
            .join(ModelVersion, Prediction.model_version_id == ModelVersion.id)
            .where(
                Prediction.host_id == event.host_id,
                Prediction.predicted_at <= event.observed_at - timedelta(seconds=60),
                Prediction.feature_window_end <= event.observed_at,
                Prediction.feature_window_end >= event.observed_at - timedelta(hours=1),
            )
        )
    ).all()
    eligible = [p for p, _ in candidates if useful_lead(p, event.observed_at) is not None]
    warnings = [
        p
        for p, cutoff in candidates
        if p.probability >= cutoff and useful_lead(p, event.observed_at) is not None
    ]
    leads = [useful_lead(p, event.observed_at) for p in warnings]
    deliveries = (
        (
            await session.execute(
                select(Prediction, Alert.delivered_at)
                .join(Incident, Incident.prediction_id == Prediction.id)
                .join(Alert, Alert.incident_id == Incident.id)
                .where(Prediction.id.in_([p.id for p in warnings]), Alert.status == "delivered")
            )
        ).all()
        if warnings
        else []
    )
    delivered_leads = [
        lead
        for p, delivered_at in deliveries
        if delivered_at is not None
        and (lead := useful_lead(p, event.observed_at, delivered_at)) is not None
    ]
    return {
        **result,
        "eligible_scores": len(eligible),
        "coverage": "Alert delivered"
        if delivered_leads
        else "Warning recorded"
        if warnings
        else "No advance warning"
        if eligible
        else "No eligible scores",
        "warning_lead_seconds": max((x for x in leads if x is not None), default=None),
        "alert_lead_seconds": max(delivered_leads, default=None),
    }


@router.get("/snapshot")
async def snapshot(request: Request) -> dict[str, Any]:
    async with async_sessionmaker(request.app.state.engine)() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        hosts = await hosts_view(session, now)
        healthy, risky = await host_counts(session, now)
        tables = {
            "predictions": Prediction,
            "incidents": Incident,
            "alerts": Alert,
            "proposals": RemediationProposal,
            "models": ModelVersion,
            "evaluations": EvaluationRun,
            "failure_events": FailureEvent,
        }
        data = {
            name: [record(row) for row in await recent(session, model)]
            for name, model in tables.items()
        }
        data["incidents"] = [
            await incident_view(session, i) for i in await recent(session, Incident)
        ]
        experiments = [
            await experiment_view(session, row) for row in await recent(session, ChaosExperiment)
        ]
        totals = {
            name: await session.scalar(select(func.count()).select_from(model))
            for name, model in {**tables, "experiments": ChaosExperiment, "hosts": Host}.items()
        }
        active = await session.scalar(
            select(func.count()).select_from(Incident).where(Incident.resolved_at.is_(None))
        )
        alerts_today = await session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.created_at >= now - timedelta(days=1))
        )
        latest_prediction = await session.scalar(
            select(Prediction).order_by(Prediction.predicted_at.desc()).limit(1)
        )
        current_model = (
            await session.get(ModelVersion, latest_prediction.model_version_id)
            if latest_prediction
            else None
        )
        return {
            "as_of": now,
            "limit": LIMIT,
            "totals": totals,
            "hosts": hosts[:1000],
            "host_limit": 1000,
            "overview": {
                "monitored_hosts": totals["hosts"],
                "healthy_hosts": healthy,
                "high_risk_hosts": risky,
                "active_incidents": active,
                "alerts_last_24h": alerts_today,
                "current_model": record(current_model) if current_model else None,
            },
            "experiments": experiments,
            **data,
            "governance": {
                "enabled": request.app.state.settings.runbookos_enabled,
                "adapter": request.app.state.settings.runbookos_adapter,
                "human_approval_required": True,
                "execution_owner": "RunbookOS",
            },
        }


async def telemetry(request: Request, host_id: str, now: datetime) -> dict[str, Any]:
    selector = (
        '{__name__=~"sentinel_host_(cpu_usage_percent|memory_usage_percent|disk_usage_percent|network_receive_bytes_per_second)",host_id='
        + json.dumps(host_id)
        + "}"
    )
    try:
        async with create_prometheus_client(request.app.state.settings, timeout=4) as client:
            responses = await asyncio.gather(
                client.get(
                    "/api/v1/query_range",
                    params={
                        "query": selector,
                        "start": now.timestamp() - 1800,
                        "end": now.timestamp(),
                        "step": 30,
                    },
                ),
                client.get(
                    "/api/v1/query",
                    params={
                        "query": "sentinel_host_sample_timestamp_seconds{host_id="
                        + json.dumps(host_id)
                        + "}"
                    },
                ),
            )
        for response in responses:
            response.raise_for_status()
            if response.json().get("status") != "success":
                raise ValueError("Query unavailable")
        series = []
        for item in responses[0].json()["data"]["result"]:
            values = [
                [float(t), float(v)]
                for t, v in item["values"]
                if math.isfinite(float(v)) and math.isfinite(float(t))
            ]
            series.append({"metric": item["metric"]["__name__"], "values": values})
        timestamps = [float(x["value"][1]) for x in responses[1].json()["data"]["result"]]
        timestamps = [stamp for stamp in timestamps if math.isfinite(stamp)]
        age = now.timestamp() - max(timestamps) if timestamps else None
        return {
            "status": "available" if age is not None and 0 <= age <= 90 else "stale",
            "sample_age_seconds": age,
            "series": series,
            "window_seconds": 1800,
        }
    except httpx.HTTPError, KeyError, ValueError, TypeError:
        return {
            "status": "unavailable",
            "series": [],
            "window_seconds": 1800,
            "sample_age_seconds": None,
        }


@router.get("/hosts/{host_id}")
async def host_detail(host_id: str, request: Request) -> dict[str, Any]:
    async with async_sessionmaker(request.app.state.engine)() as session:
        host = await session.get(Host, host_id)
        if not host:
            raise HTTPException(404, "Host not found")
        now = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        predictions = await recent(session, Prediction, host_id=host_id)
        incidents = await recent(session, Incident, host_id=host_id)
        return {
            "as_of": now,
            "host_id": host_id,
            "predictions": [record(p) for p in predictions],
            "incidents": [await incident_view(session, i) for i in incidents],
            "telemetry": await telemetry(request, host_id, now),
        }
