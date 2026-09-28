"""Authenticated, bounded demo experiments; never accepts arbitrary URLs or commands."""

import hmac
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select

from app.models import ChaosExperiment, FailureEvent, Host
from app.saas.repository import get_for_account, request_session
from app.services.chaos import ExperimentRequest, enforce_policy, enforce_scope

router = APIRouter(prefix="/chaos", tags=["controlled demo experiments"])


def injector_times(evidence: dict[str, object], requested: datetime, duration: int) -> datetime:
    started = datetime.fromisoformat(str(evidence["started_at"]))
    expires = datetime.fromisoformat(str(evidence["expires_at"]))
    if (
        started.tzinfo is None
        or expires.tzinfo is None
        or not -5 <= (started - requested).total_seconds() <= 10
        or not 0 < (expires - started).total_seconds() <= duration + 5
    ):
        raise ValueError("Injector timing does not match bounded request")
    return started


def authorize(request: Request, authorization: str | None) -> None:
    if request.app.state.settings.saas_enabled:
        from app.saas.security import require_role

        require_role(request, "OWNER", "ADMIN")
        return
    token = request.app.state.settings.chaos_control_token.get_secret_value()
    if len(token) < 32 or not hmac.compare_digest(authorization or "", "Bearer " + token):
        raise HTTPException(401, "Invalid experiment authorization")


@router.post("/experiments", status_code=201)
async def start_experiment(
    payload: ExperimentRequest, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, object]:
    authorize(request, authorization)
    settings = request.app.state.settings
    enforce_scope(settings, payload)
    async with request_session(request) as session:
        host = await get_for_account(session, Host, payload.host_id)
        enforce_policy(settings, payload, host)
        experiment = ChaosExperiment(
            host_id=payload.host_id,
            environment=settings.environment,
            failure_type=payload.failure_type,
            started_at=datetime.now(UTC),
            status="pending",
            parameters=payload.model_dump(),
            expected_degradation={
                "latency_slo_seconds": settings.observation_latency_slo_seconds,
                "http_error_status_min": 500,
                "consecutive_breaches": 3,
                "probe": "/work?units=100000",
                "target": payload.target,
            },
        )
        session.add(experiment)
        if settings.saas_enabled:
            from app.saas.auth import audit

            await session.flush()
            audit(
                session,
                request.state.principal.account_id,
                "chaos.triggered",
                experiment.id,
                request.state.principal.user_id,
            )
        await session.commit()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(
                    settings.chaos_demo_url + "/control/chaos",
                    headers={
                        "Authorization": "Bearer " + settings.chaos_control_token.get_secret_value()
                    },
                    json={
                        "experiment_id": str(experiment.id),
                        "failure_type": payload.failure_type,
                        "duration_seconds": payload.duration_seconds,
                        "latency_ms": payload.latency_ms,
                        "namespace": payload.namespace,
                    },
                )
                response.raise_for_status()
                evidence = response.json()
                if evidence.get("experiment_id") != str(experiment.id) or not evidence.get(
                    "active"
                ):
                    raise ValueError("Unconfirmed injector response")
            experiment.started_at = injector_times(
                evidence, experiment.started_at, payload.duration_seconds
            )
            experiment.status = "running"
            experiment.parameters = {
                **experiment.parameters,
                "injector_expires_at": evidence["expires_at"],
            }
            await session.commit()
        except httpx.HTTPError, ValueError, KeyError, TypeError:
            # A lost response may still apply a fault; reconcile by experiment ID.
            experiment.status = "control_unknown"
            experiment.observed_degradation = {
                "control_outcome": "unconfirmed",
                "degradation_confirmed": False,
            }
            await session.commit()
            raise HTTPException(
                502, "Injector did not confirm the request; observer will reconcile"
            ) from None
    return {
        "experiment_id": str(experiment.id),
        "status": experiment.status,
        "started_at": experiment.started_at.isoformat(),
        "parameters": experiment.parameters,
    }


@router.get("/experiments")
async def experiments(request: Request) -> list[dict[str, object]]:
    async with request_session(request) as session:
        rows = (
            await session.scalars(
                select(ChaosExperiment).order_by(ChaosExperiment.started_at.desc()).limit(100)
            )
        ).all()
        return [
            {
                "id": str(row.id),
                "host_id": row.host_id,
                "failure_type": row.failure_type,
                "status": row.status,
                "started_at": row.started_at.isoformat(),
                "ended_at": row.ended_at.isoformat() if row.ended_at else None,
                "expected_degradation": row.expected_degradation,
                "observed_degradation": row.observed_degradation,
            }
            for row in rows
        ]


@router.get("/failure-events")
async def failures(request: Request) -> list[dict[str, object]]:
    async with request_session(request) as session:
        rows = (
            await session.scalars(
                select(FailureEvent).order_by(FailureEvent.observed_at.desc()).limit(100)
            )
        ).all()
        return [
            {
                "id": str(row.id),
                "experiment_id": str(row.experiment_id) if row.experiment_id else None,
                "host_id": row.host_id,
                "failure_type": row.failure_type,
                "observed_at": row.observed_at.isoformat(),
                "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
                "recovered_at": row.recovered_at.isoformat() if row.recovered_at else None,
                "criterion": row.criterion,
            }
            for row in rows
        ]


@router.post("/experiments/{experiment_id}/cancel")
async def cancel(
    experiment_id: UUID, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, object]:
    authorize(request, authorization)
    settings = request.app.state.settings
    async with request_session(request) as session:
        row = await get_for_account(session, ChaosExperiment, experiment_id)
        if row is None:
            raise HTTPException(404, "Experiment not found")
        try:
            payload = ExperimentRequest(
                **{
                    key: value
                    for key, value in row.parameters.items()
                    if key in ExperimentRequest.model_fields
                }
            )
        except ValidationError, TypeError, AttributeError:
            raise HTTPException(
                409, "Experiment record is invalid; bounded expiry applies"
            ) from None
        if payload.host_id != row.host_id or row.environment != settings.environment:
            raise HTTPException(409, "Experiment scope does not match the record")
        enforce_policy(settings, payload, await get_for_account(session, Host, row.host_id))
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.delete(
                settings.chaos_demo_url + f"/control/chaos/{experiment_id}",
                headers={
                    "Authorization": "Bearer " + settings.chaos_control_token.get_secret_value()
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(
            502, "Cancellation not confirmed; bounded injector expiry still applies"
        ) from None
    return {"experiment_id": str(experiment_id), "active": False}
