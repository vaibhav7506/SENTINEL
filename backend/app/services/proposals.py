"""Persist a suggestion before adapter intake; keep human approval external."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import Incident, ModelVersion, Prediction, RemediationProposal
from app.saas.auth import audit
from app.services.runbookos import (
    MockRunbookOSAdapter,
    ProposalReceipt,
    ProposalRequest,
    RunbookOSAdapter,
)


class ProposalService:
    def __init__(
        self, settings: Settings, engine: AsyncEngine, adapter: RunbookOSAdapter | None = None
    ):
        self.settings = settings
        self.sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]] = async_sessionmaker(
            engine, expire_on_commit=False
        )
        self.adapter = adapter or MockRunbookOSAdapter()

    async def initialize(self) -> None:
        if not self.settings.runbookos_enabled:
            return
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(RemediationProposal)
                .where(RemediationProposal.submission_status == "sending")
                .values(
                    submission_status="unknown",
                    last_error="Interrupted intake; outcome unknown; no automatic retry",
                )
            )

    def eligible(self, prediction: Prediction, model: ModelVersion, now: datetime) -> bool:
        return (
            prediction.probability
            >= max(self.settings.runbookos_proposal_threshold, model.threshold)
            and 0
            <= (now - prediction.predicted_at).total_seconds()
            <= self.settings.inference_max_age_seconds
            and 0
            <= (now - prediction.feature_window_end).total_seconds()
            <= self.settings.inference_max_age_seconds
            and now < prediction.feature_window_end + timedelta(seconds=prediction.horizon_seconds)
        )

    async def create_pending(self) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(736284062)"))
            now = await session.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            incidents = (
                await session.scalars(
                    select(Incident).where(
                        Incident.resolved_at.is_(None), Incident.model_version_id.is_not(None)
                    )
                )
            ).all()
            for incident in incidents:
                prediction = await session.scalar(
                    select(Prediction)
                    .where(
                        Prediction.host_id == incident.host_id,
                        Prediction.model_version_id == incident.model_version_id,
                    )
                    .order_by(Prediction.feature_window_end.desc())
                    .limit(1)
                )
                model = await session.get(ModelVersion, incident.model_version_id)
                if prediction is None or model is None or not self.eligible(prediction, model, now):
                    continue
                existing = await session.scalar(
                    select(RemediationProposal.id).where(
                        RemediationProposal.incident_id == incident.id,
                        RemediationProposal.runbook_reference == "collect-diagnostics:v1",
                    )
                )
                if existing is not None:
                    continue
                warning = prediction.explanation.get(
                    "model_validation_warning",
                    "Model estimate is advisory; operational predictive accuracy is unproven",
                )
                reason = (
                    f"Recorded probability {prediction.probability:.6f} meets proposal threshold "
                    f"{self.settings.runbookos_proposal_threshold:.6f}; "
                    f"request human review of diagnostics. {warning}"
                )
                identifier = uuid4()
                request = ProposalRequest(
                    proposal_id=identifier,
                    incident_id=incident.id,
                    prediction_id=prediction.id,
                    host=incident.host_id,
                    reason=reason,
                    failure_probability=prediction.probability,
                    anomaly_score=prediction.anomaly_score,
                    forecast_valid_until=prediction.feature_window_end
                    + timedelta(seconds=prediction.horizon_seconds),
                    model_validation_warning=warning,
                )
                session.add(
                    RemediationProposal(
                        id=identifier,
                        incident_id=incident.id,
                        prediction_id=prediction.id,
                        runbook_reference=request.runbook_reference,
                        suggested_action=request.suggested_action,
                        reason=request.reason,
                        adapter_kind="mock",
                        parameters=request.model_dump(mode="json"),
                        submission_status="pending",
                        approval_status="pending",
                        execution_status="not_started",
                    )
                )
                await session.flush()
                audit(session, incident.account_id, "remediation.proposed", identifier)

    async def submit_pending(self) -> int:
        submitted = 0
        while True:
            async with self.sessions() as session, session.begin():
                proposal = await session.scalar(
                    select(RemediationProposal)
                    .where(RemediationProposal.submission_status == "pending")
                    .order_by(RemediationProposal.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if proposal is None:
                    return submitted
                incident = await session.get(Incident, proposal.incident_id)
                prediction = (
                    await session.get(Prediction, proposal.prediction_id)
                    if proposal.prediction_id
                    else None
                )
                model = (
                    await session.get(ModelVersion, prediction.model_version_id)
                    if prediction
                    else None
                )
                now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(now, datetime)
                latest_id = (
                    await session.scalar(
                        select(Prediction.id)
                        .where(
                            Prediction.host_id == prediction.host_id,
                            Prediction.model_version_id == prediction.model_version_id,
                        )
                        .order_by(Prediction.feature_window_end.desc())
                        .limit(1)
                    )
                    if prediction
                    else None
                )
                if (
                    incident is None
                    or incident.resolved_at is not None
                    or prediction is None
                    or model is None
                    or latest_id != prediction.id
                    or not self.eligible(prediction, model, now)
                ):
                    proposal.submission_status = "cancelled"
                    proposal.last_error = "Resolved, superseded, stale or expired prediction"
                    continue
                try:
                    request = ProposalRequest.model_validate(proposal.parameters)
                    if not (
                        request.proposal_id == proposal.id
                        and request.incident_id == proposal.incident_id
                        and request.prediction_id == proposal.prediction_id
                        and request.host == prediction.host_id
                        and request.failure_probability == prediction.probability
                        and request.anomaly_score == prediction.anomaly_score
                        and request.reason == proposal.reason
                        and request.forecast_valid_until
                        == prediction.feature_window_end
                        + timedelta(seconds=prediction.horizon_seconds)
                    ):
                        raise ValueError("Proposal facts do not match stored prediction")
                except Exception:
                    proposal.submission_status = "cancelled"
                    proposal.last_error = "Invalid proposal contract"
                    continue
                proposal.submission_status = "sending"
                identifier = proposal.id
            try:
                async with asyncio.timeout(5):
                    receipt = ProposalReceipt.model_validate(await self.adapter.submit(request))
                if receipt.reference != f"mock://runbookos/proposals/{identifier}":
                    raise ValueError("Unverified external adapter reference")
            except Exception:
                async with self.sessions() as session, session.begin():
                    stored = await session.get(RemediationProposal, identifier)
                    assert stored is not None
                    stored.submission_status = "unknown"
                    stored.last_error = (
                        "Adapter intake failed or response invalid; no execution claimed"
                    )
                continue
            async with self.sessions() as session, session.begin():
                stored = await session.get(RemediationProposal, identifier)
                assert stored is not None
                stored.runbookos_reference = receipt.reference
                if stored.approval_status != "approved":
                    stored.approval_status = receipt.approval_status
                stored.execution_status = receipt.execution_status
                stored.submission_status = "submitted"
                stored.submitted_at = await session.scalar(select(func.clock_timestamp()))
            submitted += 1

    async def cycle(self) -> int:
        if not self.settings.runbookos_enabled:
            return 0
        await self.create_pending()
        return await self.submit_pending()
