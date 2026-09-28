"""Durable proposal contracts and labeled historical replay in an isolated DB."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.session import create_database_engine
from app.models import Host, Incident, ModelVersion, Prediction, RemediationProposal
from app.services.proposals import ProposalService
from app.services.runbookos import MockRunbookOSAdapter

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("SENTINEL_TEST_DATABASE") != "1", reason="Requires isolated PostgreSQL"
    ),
]


class RecordingAdapter(MockRunbookOSAdapter):
    def __init__(self, fail=False, invalid=False):
        self.requests = []
        self.fail, self.invalid = fail, invalid

    async def submit(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("secret-adapter-error-must-not-leak")
        if self.invalid:
            return {
                "reference": "mock://wrong",
                "approval_status": "approved",
                "execution_status": "succeeded",
            }
        return await super().submit(request)


async def add_condition(sessions, model_id, name, probability=0.999, offset=2, horizon=600):
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        session.add(Host(id=name, name=name, environment="test", first_seen=now, last_seen=now))
        await session.flush()
        p = Prediction(
            host_id=name,
            model_version_id=model_id,
            predicted_at=now - timedelta(seconds=offset),
            feature_window_end=now - timedelta(seconds=offset),
            schema_version="fixture",
            probability=probability,
            anomaly_score=0.2,
            horizon_seconds=horizon,
            explanation={
                "model_validation_warning": "Isolated fixture; predictive performance unproven"
            },
        )
        session.add(p)
        await session.flush()
        incident = Incident(
            host_id=name,
            model_version_id=model_id,
            prediction_id=p.id,
            status="open",
            summary={"kind": "isolated proposal fixture"},
        )
        session.add(incident)
        await session.flush()
        return incident.id, p.id


async def test_durable_intake_and_failure_boundaries():
    settings = Settings()
    assert settings.postgres_db.startswith("sentinel_phase7_test_")
    settings = settings.model_copy(update={"runbookos_enabled": True})
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    adapter = RecordingAdapter()
    service = ProposalService(settings, engine, adapter)
    try:
        async with sessions() as session, session.begin():
            model = ModelVersion(
                version="phase7-boundary-fixture",
                schema_version="fixture",
                schema_hash="fixture",
                artifact_uri="fixture",
                threshold=0.8,
                training_metadata={"kind": "isolated fixture"},
            )
            session.add(model)
            await session.flush()
            model_id = model.id
        await add_condition(sessions, model_id, "eligible-fixture")
        await service.initialize()
        assert await service.cycle() == 1
        assert await service.cycle() == 0
        restarted = ProposalService(settings, engine, adapter)
        await restarted.initialize()
        assert await restarted.cycle() == 0 and len(adapter.requests) == 1
        async with sessions() as session:
            p = await session.scalar(select(RemediationProposal))
            assert p.submission_status == "submitted" and p.runbookos_reference.startswith(
                "mock://"
            )
            assert p.approval_status == "pending" and p.execution_status == "not_started"
            assert p.approved_at is None and p.executed_at is None
        for name, prob, offset, horizon in [
            ("below", 0.99, 2, 600),
            ("stale", 0.999, 121, 600),
            ("expired", 0.999, 60, 30),
            ("future", 0.999, -10, 600),
        ]:
            await add_condition(sessions, model_id, name, prob, offset, horizon)
        assert await service.cycle() == 0
        incident_id, _ = await add_condition(sessions, model_id, "resolved-during-queue")
        await service.create_pending()
        async with sessions() as session, session.begin():
            incident = await session.get(Incident, incident_id)
            incident.resolved_at = datetime.now(UTC)
        assert await service.submit_pending() == 0
        tampered_id, _ = await add_condition(sessions, model_id, "tampered-facts")
        await service.create_pending()
        async with sessions() as session, session.begin():
            tampered = await session.scalar(
                select(RemediationProposal).where(RemediationProposal.incident_id == tampered_id)
            )
            tampered.parameters = {**tampered.parameters, "failure_probability": 0.1}
        assert await service.submit_pending() == 0
        async with sessions() as session:
            tampered = await session.get(RemediationProposal, tampered.id)
            assert tampered.submission_status == "cancelled"
        async with sessions() as session:
            cancelled = await session.scalar(
                select(RemediationProposal).where(RemediationProposal.incident_id == incident_id)
            )
            assert cancelled.submission_status == "cancelled"
        _, first_id = await add_condition(sessions, model_id, "superseded")
        await service.create_pending()
        async with sessions() as session, session.begin():
            first = await session.get(Prediction, first_id)
            session.add(
                Prediction(
                    host_id=first.host_id,
                    model_version_id=model_id,
                    predicted_at=datetime.now(UTC),
                    feature_window_end=datetime.now(UTC),
                    schema_version="fixture",
                    probability=0.1,
                    anomaly_score=0.2,
                    horizon_seconds=600,
                    explanation={},
                )
            )
        assert await service.submit_pending() == 0
        uncertain_id, _ = await add_condition(sessions, model_id, "uncertain")
        broken = ProposalService(settings, engine, RecordingAdapter(fail=True))
        assert await broken.cycle() == 0
        async with sessions() as session:
            unknown = await session.scalar(
                select(RemediationProposal).where(RemediationProposal.incident_id == uncertain_id)
            )
            assert unknown.submission_status == "unknown" and "secret" not in unknown.last_error
        assert await restarted.cycle() == 0
        malicious_id, _ = await add_condition(sessions, model_id, "invalid-receipt")
        assert await ProposalService(settings, engine, RecordingAdapter(invalid=True)).cycle() == 0
        async with sessions() as session:
            invalid = await session.scalar(
                select(RemediationProposal).where(RemediationProposal.incident_id == malicious_id)
            )
            assert (
                invalid.submission_status == "unknown" and invalid.execution_status == "not_started"
            )
        interrupted_id, _ = await add_condition(sessions, model_id, "interrupted")
        await service.create_pending()
        async with sessions() as session, session.begin():
            interrupted = await session.scalar(
                select(RemediationProposal).where(RemediationProposal.incident_id == interrupted_id)
            )
            interrupted.submission_status = "sending"
        await restarted.initialize()
        assert await restarted.cycle() == 0
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RemediationProposal)
                    .where(RemediationProposal.execution_status != "not_started")
                )
                == 0
            )
        with pytest.raises(IntegrityError):
            async with sessions() as session, session.begin():
                stored = await session.get(RemediationProposal, interrupted.id)
                stored.execution_status = "running"
                await session.flush()
        # End this fixture cohort before the separate historical replay lowers its threshold.
        async with sessions() as session, session.begin():
            await session.execute(
                update(Incident)
                .where(Incident.model_version_id == model_id)
                .values(resolved_at=datetime.now(UTC))
            )
    finally:
        await engine.dispose()


async def test_actual_historical_model_to_mock_proposal():
    from inference.scoring import Scorer

    settings = Settings()
    assert settings.postgres_db.startswith("sentinel_phase7_test_")
    settings = settings.model_copy(
        update={"runbookos_enabled": True, "runbookos_proposal_threshold": 0.98}
    )
    scorer = Scorer(ROOT / settings.inference_model_path)
    scores = [
        json.loads(line)
        for line in (ROOT / settings.inference_model_path / "scores.jsonl").read_text().splitlines()
    ]
    source = next(row for row in scores if row["warning"])
    rows = [
        json.loads(line)
        for line in (scorer.dataset_path / "samples.jsonl").read_text().splitlines()
    ]
    row = next(
        r
        for r in rows
        if r["host_id"] == source["host_id"] and r["window_end"] == source["window_end"]
    )
    actual = scorer.score(row["feature_values"], False)
    assert actual["failure_probability"] == pytest.approx(source["failure_probability"], rel=1e-5)
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    adapter = RecordingAdapter()
    try:
        async with sessions() as session, session.begin():
            model = ModelVersion(
                version=scorer.metadata["version"],
                schema_version=scorer.metadata["feature_schema"]["version"],
                schema_hash=scorer.metadata["feature_schema"]["hash"],
                artifact_uri=settings.inference_model_path,
                threshold=scorer.metadata["threshold"],
                training_metadata=scorer.metadata,
            )
            session.add(model)
            await session.flush()
            model_id = model.id
        incident_id, _ = await add_condition(
            sessions, model_id, "phase7-historical-replay", actual["failure_probability"]
        )
        service = ProposalService(settings, engine, adapter)
        await service.initialize()
        assert await service.cycle() == 1
        assert await service.cycle() == 0
        async with sessions() as session:
            p = await session.scalar(
                select(RemediationProposal).where(RemediationProposal.incident_id == incident_id)
            )
            record = {
                "verified_at": datetime.now(UTC).isoformat(),
                "kind": "historical_model_replay_in_isolated_database",
                "is_live_prediction": False,
                "source_window_end": source["window_end"],
                "source_host": source["host_id"],
                "model_version": scorer.metadata["version"],
                "real_model_probability": actual["failure_probability"],
                "isolated_replay_threshold": 0.98,
                "live_default_threshold_unchanged": 0.995,
                "proposal_id": str(p.id),
                "incident_id": str(p.incident_id),
                "prediction_id": str(p.prediction_id),
                "suggested_action": p.suggested_action,
                "reason": p.reason,
                "runbookos_reference": p.runbookos_reference,
                "adapter_kind": p.adapter_kind,
                "submission_status": p.submission_status,
                "approval_status": p.approval_status,
                "execution_status": p.execution_status,
                "approved_at": None,
                "executed_at": None,
                "adapter_submissions": len(adapter.requests),
                "sentinel_executed_remediation": False,
                "real_runbookos_execution_claim": False,
            }
            assert p.execution_status == "not_started" and p.approval_status == "pending"
        (ROOT / os.getenv("SENTINEL_TEST_EVIDENCE", "docs/phase-7-proposal-demo.json")).write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        await engine.dispose()
