"""Optional isolated-Postgres check for enrichment and warning/delivery joins."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.console import experiment_view, host_counts, hosts_view, incident_view, recent
from app.core.config import Settings
from app.db.session import create_database_engine
from app.models import (
    Alert,
    ChaosExperiment,
    FailureEvent,
    Host,
    Incident,
    ModelVersion,
    Prediction,
    ServiceObservation,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("SENTINEL_TEST_DATABASE") != "1", reason="requires isolated Postgres"
)


async def test_host_list_is_bounded_but_aggregates_include_every_host():
    engine = create_database_engine(Settings(_env_file=None))
    now = datetime.now(UTC)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            hosts = [
                Host(
                    id=f"bound-{i:04}",
                    name="fixture",
                    environment="test",
                    first_seen=now,
                    last_seen=now,
                )
                for i in range(1001)
            ]
            session.add_all(hosts)
            await session.flush()
            session.add_all(
                [
                    ServiceObservation(
                        host_id=h.id,
                        request_started_at=now,
                        observed_at=now,
                        breached=False,
                        outcome="healthy",
                        latency_seconds=0.1,
                        status_code=200,
                        criteria={},
                    )
                    for h in hosts
                ]
            )
            await session.flush()
            assert len(await hosts_view(session, now)) == 1000
            healthy, high = await host_counts(session, now)
            assert healthy >= 1001 and high == 0
            await session.rollback()
    finally:
        await engine.dispose()


async def test_related_prediction_survives_list_limit_and_late_delivery_earns_no_lead():
    engine = create_database_engine(Settings(_env_file=None))
    now = datetime.now(UTC)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            host = Host(
                id="console-test",
                name="Isolated test fixture",
                environment="test",
                first_seen=now,
                last_seen=now,
            )
            model = ModelVersion(
                version="isolated-console-test",
                schema_version="test",
                schema_hash="a" * 64,
                artifact_uri="test-only",
                threshold=0.8,
                training_metadata={},
            )
            session.add_all([host, model])
            await session.flush()
            experiment = ChaosExperiment(
                host_id=host.id,
                environment="test",
                failure_type="test",
                started_at=now - timedelta(seconds=20),
                ended_at=now,
                status="completed",
                parameters={},
                expected_degradation={},
            )
            warning = Prediction(
                host_id=host.id,
                model_version_id=model.id,
                predicted_at=now - timedelta(seconds=1200),
                created_at=now - timedelta(seconds=1200),
                feature_window_end=now - timedelta(seconds=1210),
                schema_version="test",
                probability=0.9,
                anomaly_score=0.03,
                horizon_seconds=1800,
                explanation={
                    "top_drivers": [{"feature": "cpu.mean", "contribution": 0.1, "value": 20}]
                },
            )
            session.add_all([experiment, warning])
            await session.flush()
            event = FailureEvent(
                host_id=host.id,
                experiment_id=experiment.id,
                failure_type="test",
                observed_at=now - timedelta(seconds=10),
                confirmed_at=now - timedelta(seconds=5),
                recovered_at=now,
                criterion={},
                evidence={},
            )
            session.add(event)
            await session.flush()
            incident = Incident(
                host_id=host.id,
                prediction_id=warning.id,
                failure_event_id=event.id,
                status="resolved",
                summary={"summary": "Test fixture"},
                predicted_at=warning.predicted_at,
                observed_at=event.observed_at,
                confirmed_at=event.confirmed_at,
                resolved_at=now,
            )
            session.add(incident)
            await session.flush()
            alert = Alert(
                incident_id=incident.id,
                channel="local_test",
                status="delivered",
                payload={"private": "omitted"},
                delivered_at=now,
            )
            session.add(alert)
            for index in range(101):
                session.add(
                    Prediction(
                        host_id=host.id,
                        model_version_id=model.id,
                        predicted_at=now - timedelta(seconds=200 - index),
                        created_at=now - timedelta(seconds=200 - index),
                        feature_window_end=now - timedelta(seconds=210 - index),
                        schema_version="test",
                        probability=0.1,
                        anomaly_score=0.01,
                        horizon_seconds=600,
                        explanation={},
                    )
                )
            await session.flush()
            ledger = await recent(session, Prediction)
            assert len(ledger) == 100 and warning.id not in [p.id for p in ledger]
            enriched = await incident_view(session, incident)
            assert enriched["prediction"]["id"] == warning.id
            assert enriched["prediction"]["explanation"] == warning.explanation
            assert enriched["failure_event"]["confirmed_at"] == event.confirmed_at
            assert enriched["alerts"][0]["status"] == "delivered"
            assert "payload" not in enriched["alerts"][0]
            outcome = await experiment_view(session, experiment)
            assert outcome["eligible_scores"] == 102
            assert outcome["coverage"] == "Warning recorded"
            assert outcome["warning_lead_seconds"] == 1190
            assert outcome["alert_lead_seconds"] is None
            alert.delivered_at = now - timedelta(seconds=120)
            await session.flush()
            outcome = await experiment_view(session, experiment)
            assert outcome["coverage"] == "Alert delivered"
            assert outcome["alert_lead_seconds"] == 110
            # This database is disposable; rollback even after all assertions pass.
            await session.rollback()
    finally:
        await engine.dispose()
