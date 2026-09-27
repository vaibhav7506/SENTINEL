"""Persistence fixtures only in an explicitly isolated Phase 6 test database."""

import copy
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.session import create_database_engine
from app.models import Alert, FeatureWindow, Host, Incident, ModelVersion, Prediction
from inference.worker import Worker


class FixtureScorer:
    metadata = {
        "version": "phase6-contract-fixture",
        "threshold": 0.5,
        "horizon_seconds": 600,
        "feature_schema": {
            "version": "fixture",
            "hash": "fixture",
            "names": ["cpu.latest"],
            "window_seconds": 900,
        },
    }

    def score(self, values, explain=True):
        return {
            "failure_probability": 0.9 if values[0] else 0.1,
            "anomaly_score": 0.2,
            "top_drivers": [],
            "shap": {"status": "contract_fixture"},
        }


@pytest.mark.skipif(
    os.getenv("SENTINEL_TEST_DATABASE") != "1", reason="Requires isolated PostgreSQL"
)
@pytest.mark.asyncio
async def test_persistent_dedup_restart_and_risk_clear():
    settings = Settings()
    assert settings.postgres_db.startswith("sentinel_phase6_test_")
    # model_copy does not validate secrets; construct the explicit fixture settings instead.
    from pydantic import SecretStr

    settings = settings.model_copy(
        update={
            "alert_generic_webhook_url": SecretStr("http://127.0.0.1:8091/generic"),
            "alert_slack_webhook_url": SecretStr(""),
            "llm_provider": "disabled",
        }
    )
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    scorer = FixtureScorer()
    now = datetime.now(UTC)
    received = []

    async def handler(request):
        received.append(request.headers["Idempotency-Key"])
        return httpx.Response(200)

    def window(end, value):
        return FeatureWindow(
            host_id="phase6-fixture",
            window_start=end - timedelta(seconds=900),
            window_end=end,
            schema_version="fixture",
            schema_hash="fixture",
            feature_names=["cpu.latest"],
            feature_values=[value],
            features={"cpu.latest": value},
            quality={"metrics": {"cpu_usage_percent": {"coverage_seconds": 800}}},
        )

    try:
        async with sessions() as session, session.begin():
            session.add(
                Host(
                    id="phase6-fixture",
                    name="Contract fixture",
                    environment="test",
                    first_seen=now,
                    last_seen=now,
                    service_job="fixture-service",
                )
            )
            session.add(
                ModelVersion(
                    version=scorer.metadata["version"],
                    schema_version="fixture",
                    schema_hash="fixture",
                    artifact_uri="explicit-contract-fixture",
                    threshold=0.5,
                    training_metadata=scorer.metadata,
                )
            )
            await session.flush()
            session.add(window(now - timedelta(seconds=50), 1.0))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            worker = Worker(settings, engine, scorer, client, CollectorRegistry())
            await worker.initialize()
            assert await worker.cycle() == 1
            assert await worker.cycle() == 0
            async with sessions() as session, session.begin():
                session.add(window(now - timedelta(seconds=40), 1.0))
            assert await worker.cycle() == 1
            assert len(received) == 1
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Incident)) == 1
                assert await session.scalar(select(func.count()).select_from(Alert)) == 1
                prediction = await session.scalar(
                    select(Prediction).order_by(Prediction.predicted_at.desc())
                )
                assert prediction.explanation["shap"]["status"] == "existing_condition"
            restarted = Worker(settings, engine, scorer, client, CollectorRegistry())
            await restarted.initialize()
            assert await restarted.cycle() == 0
            assert len(received) == 1
            for offset, value in ((38, 0.0), (36, 1.0)):
                async with sessions() as session, session.begin():
                    session.add(window(now - timedelta(seconds=offset), value))
                assert await restarted.cycle() == 1
            async with sessions() as session:
                renewed = await session.scalar(
                    select(Prediction).order_by(Prediction.feature_window_end.desc())
                )
                assert renewed.explanation["shap"]["status"] == "contract_fixture"
                assert await session.scalar(select(func.count()).select_from(Alert)) == 1
            assert restarted.explanations._value.get() == 1
            for offset in (30, 20, 10):
                async with sessions() as session, session.begin():
                    session.add(window(now - timedelta(seconds=offset), 0.0))
                assert await restarted.cycle() == 1
            async with sessions() as session, session.begin():
                incident = await session.scalar(select(Incident))
                assert incident.status == "risk_cleared" and incident.resolved_at is not None
                session.add(
                    Alert(
                        incident_id=incident.id,
                        channel="slack",
                        status="sending",
                        payload={"kind": "explicit-contract-fixture"},
                    )
                )
            await restarted.initialize()
            async with sessions() as session:
                interrupted = await session.scalar(select(Alert).where(Alert.channel == "slack"))
                assert interrupted.status == "unknown" and interrupted.delivered_at is None
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    os.getenv("SENTINEL_TEST_DATABASE") != "1", reason="Requires isolated PostgreSQL"
)
@pytest.mark.asyncio
async def test_expired_forecast_closes_without_claiming_recovery():
    settings = Settings()
    assert settings.postgres_db.startswith("sentinel_phase6_test_")
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    scorer = FixtureScorer()
    scorer.metadata = copy.deepcopy(scorer.metadata)
    scorer.metadata["version"] = "phase9-expiry-fixture"
    try:
        async with sessions() as session, session.begin():
            model = ModelVersion(
                version=scorer.metadata["version"],
                schema_version="fixture",
                schema_hash="fixture",
                artifact_uri="fixture",
                threshold=0.5,
                training_metadata=scorer.metadata,
            )
            session.add(model)
            session.add(
                Host(
                    id="phase9-disappeared",
                    name="fixture",
                    environment="test",
                    first_seen=now,
                    last_seen=now,
                )
            )
            await session.flush()
            incident = Incident(
                host_id="phase9-disappeared",
                model_version_id=model.id,
                predicted_at=now - timedelta(seconds=1000),
                status="open",
                summary={"forecast_valid_until": (now - timedelta(seconds=30)).isoformat()},
            )
            session.add(incident)
            await session.flush()
            session.add(
                Alert(
                    incident_id=incident.id,
                    channel="generic",
                    status="pending",
                    payload={},
                    next_attempt_at=now,
                )
            )
            identifier = incident.id
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ) as client:
            worker = Worker(settings, engine, scorer, client, CollectorRegistry())
            await worker.initialize()
            await worker.correlate()
        async with sessions() as session:
            incident = await session.get(Incident, identifier)
            assert (
                incident.status == "forecast_expired_unconfirmed"
                and incident.resolved_at is not None
            )
            assert incident.failure_event_id is None and incident.observed_at is None
            alert = await session.scalar(select(Alert).where(Alert.incident_id == identifier))
            assert alert.status == "cancelled" and alert.delivered_at is None
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    os.getenv("SENTINEL_TEST_DATABASE") != "1", reason="Requires isolated PostgreSQL"
)
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["retry", "unknown"])
async def test_delivery_failure_exhaustion_and_restart_do_not_duplicate(outcome):
    from pydantic import SecretStr

    settings = Settings()
    assert settings.postgres_db.startswith("sentinel_phase6_test_")
    settings = settings.model_copy(
        update={
            "alert_generic_webhook_url": SecretStr("http://127.0.0.1:8091/generic"),
            "alert_slack_webhook_url": SecretStr(""),
            "llm_provider": "disabled",
            "runbookos_enabled": False,
        }
    )
    scorer = FixtureScorer()
    scorer.metadata = copy.deepcopy(scorer.metadata)
    scorer.metadata["version"] = "phase9-delivery-" + outcome
    feature_name = "phase9.cpu." + outcome
    scorer.metadata["feature_schema"]["names"] = [feature_name]
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    host_id = "phase9-delivery-" + outcome
    attempts = []

    def handle(request):
        attempts.append(request.headers["Idempotency-Key"])
        if outcome == "unknown":
            raise httpx.ConnectError("private-destination-error")
        return httpx.Response(503)

    try:
        async with sessions() as session, session.begin():
            session.add_all(
                [
                    Host(
                        id=host_id,
                        name="failure fixture",
                        environment="test",
                        first_seen=now,
                        last_seen=now,
                    ),
                    ModelVersion(
                        version=scorer.metadata["version"],
                        schema_version="fixture",
                        schema_hash="fixture",
                        artifact_uri="fixture",
                        threshold=0.5,
                        training_metadata=scorer.metadata,
                    ),
                ]
            )
            await session.flush()
            session.add(
                FeatureWindow(
                    host_id=host_id,
                    window_end=now,
                    window_start=now - timedelta(seconds=900),
                    schema_version="fixture",
                    schema_hash="fixture",
                    feature_names=[feature_name],
                    feature_values=[1.0],
                    features={feature_name: 1.0},
                    quality={"metrics": {"cpu_usage_percent": {"coverage_seconds": 800}}},
                )
            )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            worker = Worker(settings, engine, scorer, client, CollectorRegistry())
            await worker.initialize()
            assert await worker.cycle() == 1
            assert (
                worker.predictions._value.get()
                == worker.high_risk._value.get()
                == worker.alerts._value.get()
                == 1
            )
            async with sessions() as session:
                incident = await session.scalar(select(Incident).where(Incident.host_id == host_id))
                alert = await session.scalar(select(Alert).where(Alert.incident_id == incident.id))
                identifier = alert.id
            if outcome == "retry":
                for _ in range(2):
                    async with sessions() as session, session.begin():
                        alert = await session.get(Alert, identifier)
                        alert.next_attempt_at = datetime.now(UTC)
                    await worker.send_pending()
            restarted = Worker(settings, engine, scorer, client, CollectorRegistry())
            await restarted.initialize()
            assert await restarted.cycle() == 0
            async with sessions() as session:
                alert = await session.get(Alert, identifier)
                assert alert.status == ("failed" if outcome == "retry" else "unknown")
                assert alert.attempt_count == (3 if outcome == "retry" else 1)
                assert alert.delivered_at is None and "private" not in alert.last_error
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Alert)
                        .where(Alert.incident_id == incident.id)
                    )
                    == 1
                )
            assert len(set(attempts)) == 1
            assert worker.alert_failures._value.get() == len(attempts)
    finally:
        await engine.dispose()
