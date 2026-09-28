"""Dependency failures affect only disposable fixtures, never shared local services."""
# ruff: noqa: F811

from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis

from app.db.session import create_database_engine
from app.main import create_app
from backend.tests.test_saas_database import enroll_host, pytestmark, tenancy  # noqa: F401


async def test_redis_database_failure_recovery_and_api_restart(tenancy):
    app, client, _, identities = tenancy
    _, host, _ = await enroll_host(client)
    payload = {
        "schema_version": 1,
        "samples": [
            {"timestamp": datetime.now(UTC).isoformat(), "values": {"cpu_usage_percent": 12.0}}
        ],
    }
    headers = {"Authorization": "Bearer " + host["credential"]}
    redis = app.state.redis
    unavailable = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    app.state.redis = unavailable
    try:
        response = await client.post("/agent/v1/metrics", json=payload, headers=headers)
        assert response.status_code == 503
        assert app.state.ingestion_rejections.labels("limiter_unavailable")._value.get() == 1
    finally:
        app.state.redis = redis
        await unavailable.aclose()
    assert (
        await client.post("/agent/v1/metrics", json=payload, headers=headers)
    ).status_code == 200
    engine = app.state.engine
    unavailable_engine = create_database_engine(
        app.state.settings.model_copy(update={"postgres_port": 1})
    )
    app.state.engine = unavailable_engine
    try:
        assert (await client.get("/auth/me")).status_code == 503
        assert app.state.database_errors._value.get() >= 1
    finally:
        app.state.engine = engine
        await unavailable_engine.dispose()
    assert (await client.get("/auth/me")).json()["account"]["id"] == identities[0][0]["account"][
        "id"
    ]
    restarted = create_app(app.state.settings)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(restarted),
            base_url="http://testserver",
            cookies=client.cookies,
        ) as other:
            assert (await other.get("/hosts")).json()[0]["id"] == host["host_id"]
            assert len((await other.get(f"/hosts/{host['host_id']}/metrics")).json()) == 1
            # Same payload is a replay, never a second telemetry row.
            replay = await other.post("/agent/v1/metrics", json=payload, headers=headers)
            assert (
                replay.json()["accepted_samples"] == 0 and replay.json()["duplicate_samples"] == 1
            )


async def test_shared_account_rate_bucket_across_api_replicas(tenancy):
    app, client, _, _ = tenancy
    _, first, _ = await enroll_host(client)
    _, second, _ = await enroll_host(client)
    settings = app.state.settings.model_copy(
        update={"ingestion_account_burst": 1, "ingestion_account_per_minute": 2}
    )
    app.state.settings = settings
    replica = create_app(settings)
    payload = {
        "schema_version": 1,
        "samples": [
            {"timestamp": datetime.now(UTC).isoformat(), "values": {"cpu_usage_percent": 1.0}}
        ],
    }
    assert (
        await client.post(
            "/agent/v1/metrics",
            json=payload,
            headers={"Authorization": "Bearer " + first["credential"]},
        )
    ).status_code == 200
    async with replica.router.lifespan_context(replica):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(replica), base_url="http://testserver"
        ) as other:
            blocked = await other.post(
                "/agent/v1/metrics",
                json=payload,
                headers={"Authorization": "Bearer " + second["credential"]},
            )
            assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
