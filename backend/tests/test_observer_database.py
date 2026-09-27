"""Opt-in DB detector test with HTTP fixtures, separate from real acceptance runs."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.session import create_database_engine
from app.models import ChaosExperiment, FailureEvent, Host, ServiceObservation
from app.workers.observer import observe


@pytest.mark.skipif(
    os.getenv("SENTINEL_TEST_DATABASE") != "1", reason="Requires isolated test database"
)
async def test_detector_confirms_only_observed_breaches_and_recovery():
    settings = Settings()
    assert settings.postgres_db.startswith("sentinel_phase3_test_")
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    identifier = uuid4()
    started = datetime.now(UTC) - timedelta(seconds=20)
    active = [True]
    status = [503]

    def handle(request):
        if request.url.path == "/control/chaos":
            return httpx.Response(
                200,
                json={
                    "active": active[0],
                    "experiment_id": str(identifier),
                    "started_at": started.isoformat(),
                    "expires_at": (started + timedelta(seconds=45)).isoformat(),
                },
            )
        return httpx.Response(status[0], json={"fixture": True})

    try:
        async with factory() as session:
            session.add(
                Host(
                    id="docker-vm",
                    name="Isolated fixture",
                    environment="test",
                    service_job="demo-service",
                    first_seen=started,
                    last_seen=started,
                )
            )
            await session.flush()
            session.add(
                ChaosExperiment(
                    id=identifier,
                    host_id="docker-vm",
                    environment="test",
                    failure_type="http_error",
                    started_at=started,
                    status="running",
                    parameters={},
                    expected_degradation={},
                )
            )
            await session.commit()
        async with httpx.AsyncClient(
            base_url="http://demo", transport=httpx.MockTransport(handle)
        ) as client:
            await observe(engine, client, settings)
            await observe(engine, client, settings)
            async with factory() as session:
                assert list((await session.scalars(select(FailureEvent))).all()) == []
            await observe(engine, client, settings)
            async with factory() as session:
                event = (await session.scalars(select(FailureEvent))).one()
                probes = list(
                    (
                        await session.scalars(
                            select(ServiceObservation).order_by(ServiceObservation.observed_at)
                        )
                    ).all()
                )
                assert event.observed_at == probes[0].observed_at
                assert event.confirmed_at == probes[-1].observed_at
                assert event.recovered_at is None
                assert event.experiment_id == identifier
                assert event.evidence["status_codes"] == [503, 503, 503]
                assert all(
                    probe.criteria["latency_slo_seconds"]
                    == settings.observation_latency_slo_seconds
                    for probe in probes
                )
            status[0] = 200
            await observe(engine, client, settings)
            await observe(engine, client, settings)
            async with factory() as session:
                assert (await session.scalars(select(FailureEvent))).one().recovered_at is None
            await observe(engine, client, settings)
            active[0] = False
            await observe(engine, client, settings)
            async with factory() as session:
                event = (await session.scalars(select(FailureEvent))).one()
                experiment = await session.get(ChaosExperiment, identifier)
                assert event.recovered_at > event.confirmed_at
                assert experiment.status == "completed"
                assert experiment.ended_at is not None
                assert experiment.observed_degradation["degradation_confirmed"] is True
    finally:
        await engine.dispose()
