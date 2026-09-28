"""100-host synthetic load and adversarial fixtures: never live ML performance evidence."""

import asyncio
import json
import os
import secrets
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.main import create_app
from app.models import Alert, FeatureWindow, Incident, ModelVersion, Prediction
from app.saas.models import AlertChannel, MetricSample
from inference.push_runner import Runner
from inference.scoring import ROOT, Scorer
from inference.tests.test_database import FixtureScorer
from inference.worker import Worker

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("SENTINEL_TEST_DATABASE") != "1", reason="Disposable DB/Redis required"
    ),
]


def csrf(client):
    return {"X-CSRF-Token": client.cookies["sentinel_csrf"]}


async def enqueue_retry(container, task):
    source = (
        "import json,sys; from celery_app import infer; "
        "print(infer.delay(**json.load(sys.stdin)).id)"
    )
    result = await asyncio.to_thread(
        subprocess.run,
        ["docker", "exec", "-i", container, "/queue/.venv/bin/python", "-c", source],
        input=json.dumps(task),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


async def test_100_hosts_real_celery_beat_and_tenant_isolation():
    settings = Settings(
        _env_file=None, saas_enabled=True, saas_allowed_origins=["http://testserver"]
    )
    assert settings.postgres_db.startswith("sentinel_phase3_push_test_")
    app = create_app(settings)
    clients, identities, latencies = [], [], []
    async with app.router.lifespan_context(app):
        sessions = async_sessionmaker(app.state.engine, expire_on_commit=False)
        scorer = Scorer(ROOT / settings.inference_model_path)
        async with sessions() as db, db.begin():
            model = ModelVersion(
                version=scorer.metadata["version"],
                schema_version=scorer.metadata["feature_schema"]["version"],
                schema_hash=scorer.metadata["feature_schema"]["hash"],
                artifact_uri=settings.inference_model_path,
                threshold=scorer.metadata["threshold"],
                training_metadata=scorer.metadata,
            )
            db.add(model)
        try:
            for account in range(10):
                client = httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                )
                clients.append(client)
                registered = await client.post(
                    "/auth/register",
                    json={
                        "email": secrets.token_hex(12) + "@fixture.test",
                        "password": secrets.token_urlsafe(24),
                        "account_name": f"Synthetic account {account}",
                    },
                )
                assert registered.status_code == 201, registered.text
                account_id = registered.json()["account"]["id"]
                for host in range(10):
                    issued = await client.post(
                        "/hosts/enrollment-tokens", json={}, headers=csrf(client)
                    )
                    enrolled = await client.post(
                        "/agent/enroll",
                        json={
                            "enrollment_token": issued.json()["token"],
                            "hostname": f"synthetic-{account}-{host}",
                        },
                    )
                    assert enrolled.status_code == 201, enrolled.text
                    identities.append((client, account_id, enrolled.json()))
            end = datetime.fromtimestamp(int(time.time()) // 60 * 60, UTC)
            for index, (client, _account, identity) in enumerate(identities):
                samples = [
                    {
                        "timestamp": (end - timedelta(seconds=offset)).isoformat(),
                        "values": {
                            "cpu_usage_percent": float(10 + index % 40),
                            "memory_usage_percent": 40 + index % 20,
                            "memory_available_bytes": 8e9,
                            "memory_capacity_bytes": 16e9,
                            "disk_usage_percent": 30,
                            "disk_capacity_bytes": 1e12,
                            "disk_read_bytes_per_second": 100000,
                            "disk_write_bytes_per_second": 10000,
                            "network_receive_bytes_per_second": 1000,
                            "network_transmit_bytes_per_second": 2000,
                            "process_count": 150,
                            "uptime_seconds": 100000 - offset,
                        },
                    }
                    for offset in range(870, 0, -30)
                ]
                started = time.perf_counter()
                response = await client.post(
                    "/agent/v1/metrics",
                    json={
                        "schema_version": 1,
                        "samples": samples,
                        "reporting_interval_seconds": 30,
                    },
                    headers={"Authorization": "Bearer " + identity["credential"]},
                )
                latencies.append(time.perf_counter() - started)
                assert response.status_code == 200 and response.json()["accepted_samples"] == 29, (
                    response.text
                )
            # Real Beat enumerates enrolled hosts and real prefork workers score frozen artifacts.
            deadline = time.monotonic() + 150
            peak_queue = 0
            while time.monotonic() < deadline:
                peak_queue = max(peak_queue, await app.state.redis.llen("phase3-fixture"))
                async with sessions() as db:
                    count = await db.scalar(
                        select(func.count(func.distinct(Prediction.host_id)))
                        .select_from(Prediction)
                        .where(Prediction.model_version_id == model.id)
                    )
                if count >= 100:
                    break
                await asyncio.sleep(1)
            assert count == 100, f"Expected 100 real frozen-model scores, got {count}"
            for index, client in enumerate(clients):
                hosts = (await client.get("/hosts")).json()
                assert len(hosts) == 10 and all(h["status"] == "Online" for h in hosts)
                predictions = (await client.get("/predictions?limit=100")).json()
                assert len(predictions) >= 10
                assert all(p["account_id"] == identities[index * 10][1] for p in predictions)
                other = identities[((index + 1) % 10) * 10][2]["host_id"]
                for suffix in ("", "/metrics", "/features", "/credentials"):
                    assert (await client.get("/hosts/" + other + suffix)).status_code == 404
                assert (await client.get("/console/hosts/" + other)).status_code == 404
            runner = Runner()
            try:
                wrong = await runner.infer(
                    {
                        "account_id": identities[0][1],
                        "host_id": identities[10][2]["host_id"],
                        "window_end": end.isoformat(),
                    }
                )
                assert wrong["status"] == "host_unavailable" and runner.scorer is None
            finally:
                await runner.close()
            # A held lease causes a real Celery retry, which must remain idempotent.
            client, account, identity = identities[0]
            async with sessions() as db:
                retry_end = await db.scalar(
                    select(Prediction.feature_window_end)
                    .where(
                        Prediction.host_id == identity["host_id"],
                        Prediction.model_version_id == model.id,
                    )
                    .order_by(Prediction.feature_window_end.desc())
                    .limit(1)
                )
                before_retry = await db.scalar(
                    select(func.count())
                    .select_from(Prediction)
                    .where(
                        Prediction.host_id == identity["host_id"],
                        Prediction.feature_window_end == retry_end,
                        Prediction.model_version_id == model.id,
                    )
                )
            task = {
                "account_id": account,
                "host_id": identity["host_id"],
                "window_end": retry_end.isoformat(),
            }
            async with app.state.engine.connect() as lease:
                await lease.execute(
                    text("SELECT pg_advisory_lock(hashtextextended(:host,17))"),
                    {"host": account + ":" + identity["host_id"]},
                )
                await lease.commit()
                identifier = await enqueue_retry(os.environ["SENTINEL_FIXTURE_WORKER"], task)
                retried = False
                for _ in range(20):
                    value = await app.state.redis.get("celery-task-meta-" + identifier)
                    if value and json.loads(value)["status"] == "RETRY":
                        retried = True
                        break
                    await asyncio.sleep(0.25)
                await lease.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:host,17))"),
                    {"host": account + ":" + identity["host_id"]},
                )
                await lease.commit()
            assert retried, "Real transient task failure must enter Celery RETRY"
            for _ in range(40):
                value = await app.state.redis.get("celery-task-meta-" + identifier)
                if value and json.loads(value)["status"] == "SUCCESS":
                    break
                await asyncio.sleep(0.25)
            assert json.loads(value)["result"]["status"] == "duplicate"
            async with sessions() as db:
                assert (
                    await db.scalar(
                        select(func.count(func.distinct(Prediction.host_id)))
                        .select_from(Prediction)
                        .where(Prediction.model_version_id == model.id)
                    )
                    == 100
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(Prediction)
                        .where(
                            Prediction.host_id == identity["host_id"],
                            Prediction.feature_window_end == retry_end,
                            Prediction.model_version_id == model.id,
                        )
                    )
                    == before_retry
                    == 1
                )
                assert await db.scalar(select(func.count()).select_from(MetricSample)) == 2900
                prediction_latencies = sorted(
                    await db.scalars(
                        select(
                            func.extract(
                                "epoch", Prediction.predicted_at - Prediction.feature_window_end
                            )
                        ).where(Prediction.model_version_id == model.id)
                    )
                )
                assert prediction_latencies and prediction_latencies[-1] <= 90
                latency_start = time.perf_counter()
                await db.execute(
                    text("SELECT count(*) FROM metrics WHERE account_id=:a AND host_id=:h"),
                    {"a": UUID(account), "h": identity["host_id"]},
                )
                query_seconds = time.perf_counter() - latency_start
            # Independent credential bucket + shared account bucket, across API replicas.
            for index, identity in enumerate((identities[0][2], identities[1][2])):
                await app.state.redis.hset(
                    f"sentinel:{{{account}}}:host:{identity['credential_id']}",
                    mapping={"tokens": 0 if index == 0 else 30, "time": time.time()},
                )
                response = await client.post(
                    "/agent/v1/metrics",
                    json={
                        "schema_version": 1,
                        "samples": [
                            {
                                "timestamp": datetime.now(UTC).isoformat(),
                                "values": {"cpu_usage_percent": 1},
                            }
                        ],
                    },
                    headers={"Authorization": "Bearer " + identity["credential"]},
                )
                assert response.status_code == (429 if index == 0 else 200)
            latencies.sort()
            output = {
                "scope": "Disposable synthetic 100-host simulation; not production benchmark",
                "accounts": 10,
                "hosts": 100,
                "samples": 2900,
                "hosts_with_actual_frozen_model_predictions": 100,
                "p50_ingestion_seconds": latencies[49],
                "p95_ingestion_seconds": latencies[94],
                "ingestion_requests_per_second": 100 / sum(latencies),
                "ingestion_errors": 0,
                "maximum_sampled_queue_depth": peak_queue,
                "prediction_age_p50_seconds": float(
                    prediction_latencies[len(prediction_latencies) // 2]
                ),
                "prediction_age_p95_seconds": float(
                    prediction_latencies[int(len(prediction_latencies) * 0.95)]
                ),
                "database_count_query_seconds": query_seconds,
                "real_beat_and_celery_verified": True,
                "real_retry_idempotency_verified": True,
                "adversarial_account_host_checks": 50,
                "cross_account_job_rejected_before_model_load": True,
                "predictive_success_claim": False,
            }
            Path(".runtime/phase3/load.json").write_text(
                json.dumps(output, indent=2), encoding="utf-8"
            )
        finally:
            for client in clients:
                await client.aclose()


async def test_account_alert_routing_restart_and_llm_failure():
    settings = Settings(_env_file=None, saas_enabled=True)
    app = create_app(settings)
    deliveries = []

    async def receiver(request):
        payload = json.loads(request.content)
        deliveries.append((request.url.path, payload))
        return httpx.Response(200)

    class FailedProvider:
        async def select(self, facts):
            raise RuntimeError("Unavailable fixture provider")

    async with app.router.lifespan_context(app):
        sessions = async_sessionmaker(app.state.engine, expire_on_commit=False)
        scorer = FixtureScorer()
        scorer.metadata = json.loads(json.dumps(scorer.metadata))
        scorer.metadata["version"] = "phase3-isolated-routing-" + uuid4().hex
        async with sessions() as db, db.begin():
            model = ModelVersion(
                version=scorer.metadata["version"],
                schema_version="fixture",
                schema_hash="fixture",
                artifact_uri="contract-fixture-only",
                threshold=0.5,
                training_metadata=scorer.metadata,
            )
            db.add(model)
        identities = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as delivery:
            for index in range(2):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    account = (
                        await client.post(
                            "/auth/register",
                            json={
                                "email": secrets.token_hex(12) + "@routing.test",
                                "password": secrets.token_urlsafe(24),
                                "account_name": "Routing fixture",
                            },
                        )
                    ).json()["account"]["id"]
                    issued = await client.post(
                        "/hosts/enrollment-tokens", json={}, headers=csrf(client)
                    )
                    enrolled = (
                        await client.post(
                            "/agent/enroll",
                            json={
                                "enrollment_token": issued.json()["token"],
                                "hostname": "routing-only",
                            },
                        )
                    ).json()
                    url = f"http://127.0.0.1:8091/account-{index}"
                    channel = await client.post(
                        "/alert-channels",
                        headers=csrf(client),
                        json={"name": "Test receiver", "kind": "generic", "destination": url},
                    )
                    assert channel.status_code == 201, channel.text
                    assert "destination" not in channel.json()
                    now = datetime.now(UTC)
                    async with sessions() as db, db.begin():
                        db.add(
                            FeatureWindow(
                                account_id=UUID(account),
                                host_id=enrolled["host_id"],
                                window_start=now - timedelta(seconds=900),
                                window_end=now,
                                schema_version="fixture",
                                schema_hash="fixture",
                                feature_names=["cpu.latest"],
                                feature_values=[1.0],
                                features={"cpu.latest": 1.0},
                                quality={
                                    "metrics": {"cpu_usage_percent": {"coverage_seconds": 800}}
                                },
                            )
                        )
                    worker = Worker(
                        settings,
                        app.state.engine,
                        scorer,
                        delivery,
                        CollectorRegistry(),
                        account_id=UUID(account),
                        host_id=enrolled["host_id"],
                        window_end=now,
                    )
                    worker.provider = FailedProvider()
                    await worker.initialize()
                    assert await worker.score_windows() == 1
                    assert await worker.score_windows() == 0
                    await worker.send_pending()
                    await worker.send_pending()
                    identities.append((UUID(account), enrolled["host_id"], channel.json()["id"]))
                    restarted = Worker(
                        settings,
                        app.state.engine,
                        scorer,
                        delivery,
                        CollectorRegistry(),
                        account_id=UUID(account),
                        host_id=enrolled["host_id"],
                        window_end=now,
                    )
                    await restarted.initialize()
                    assert await restarted.score_windows() == 0
                    await restarted.send_pending()
            assert len(deliveries) == 2
            for index, (path, payload) in enumerate(deliveries):
                assert path == f"/account-{index}" and payload["host"] == identities[index][1]
                assert payload["summary"]["source"] == "deterministic_fallback"
                assert identities[1 - index][1] not in json.dumps(payload)
            async with sessions() as db:
                alerts = (
                    await db.scalars(
                        select(Alert).where(
                            Alert.account_id.in_([identity[0] for identity in identities])
                        )
                    )
                ).all()
                assert len(alerts) == 2 and all(a.status == "delivered" for a in alerts)
                for alert in alerts:
                    owner = next(i for i in identities if i[0] == alert.account_id)
                    assert alert.channel == "generic:" + owner[2]
                    incident = await db.get(Incident, alert.incident_id)
                    assert incident.account_id == owner[0] and incident.host_id == owner[1]
                for _, _, channel in identities:
                    stored = await db.get(AlertChannel, UUID(channel))
                    assert stored.destination.startswith("enc:v1:")
