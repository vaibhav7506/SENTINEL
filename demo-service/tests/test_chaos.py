from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.chaos import FaultRequest, FaultState, state
from app.main import app

TOKEN = "x" * 40


@pytest.fixture
def enabled(monkeypatch):
    for key, value in {
        "ENVIRONMENT": "test",
        "CHAOS_ENABLED": "true",
        "CHAOS_ALLOWED_ENVIRONMENTS": '["test"]',
        "CHAOS_ALLOWED_NAMESPACES": '["sentinel-demo"]',
        "CHAOS_ALLOWED_TARGETS": '["demo-service"]',
        "CHAOS_CONTROL_TOKEN": TOKEN,
    }.items():
        monkeypatch.setenv(key, value)
    state.deadline = 0
    app.state.chaos_starts.clear()
    yield
    state.deadline = 0


async def test_direct_injector_bounds_input_and_auth_before_validation(enabled):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://demo"
    ) as client:
        assert (await client.post("/control/chaos", json={"private": "token"})).status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        assert (
            await client.post("/control/chaos", content=b"x" * 16385, headers=headers)
        ).status_code == 413
        response = await client.post("/control/chaos", json={"private": "token"}, headers=headers)
        assert response.status_code == 422 and "token" not in response.text


async def test_direct_injector_rate_limit_preserves_cancellation(enabled):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://demo"
    ) as client:
        headers = {"Authorization": "Bearer " + TOKEN}
        for _ in range(6):
            assert (
                await client.post("/control/chaos", json={}, headers=headers)
            ).status_code == 422
        assert (await client.post("/control/chaos", json={}, headers=headers)).status_code == 429
        assert (
            await client.delete("/control/chaos/" + str(uuid4()), headers=headers)
        ).status_code == 404


async def test_real_error_injection_auth_expiry_and_cancellation(enabled):
    identifier = str(uuid4())
    payload = {
        "experiment_id": identifier,
        "failure_type": "http_error",
        "duration_seconds": 10,
        "namespace": "sentinel-demo",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://demo"
    ) as client:
        assert (await client.post("/control/chaos", json=payload)).status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        assert (
            await client.post("/control/chaos", json=payload, headers=headers)
        ).status_code == 200
        assert (
            await client.post("/control/chaos", json=payload, headers=headers)
        ).status_code == 409
        assert (await client.get("/work?units=1")).status_code == 503
        assert (await client.get("/health")).status_code == 200
        assert (
            await client.delete("/control/chaos/" + identifier, headers=headers)
        ).status_code == 200
        assert (await client.get("/work?units=1")).status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"CHAOS_ENABLED": "false"},
        {"ENVIRONMENT": "production", "CHAOS_ALLOWED_ENVIRONMENTS": '["production"]'},
        {"CHAOS_ALLOWED_NAMESPACES": "[]"},
        {"CHAOS_ALLOWED_TARGETS": "[]"},
        {"CHAOS_ALLOWED_ENVIRONMENTS": "[]"},
        {"CHAOS_ALLOWED_ENVIRONMENTS": "null"},
    ],
)
async def test_injector_enforces_independent_allowlists(enabled, monkeypatch, changes):
    for key, value in changes.items():
        monkeypatch.setenv(key, value)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://demo"
    ) as client:
        response = await client.post(
            "/control/chaos",
            headers={"Authorization": "Bearer " + TOKEN},
            json={
                "experiment_id": str(uuid4()),
                "failure_type": "http_error",
                "duration_seconds": 10,
                "namespace": "sentinel-demo",
            },
        )
        assert response.status_code == 403
        assert (await client.get("/work?units=1")).status_code == 200


def test_fault_ttl_uses_monotonic_clock_and_cannot_exceed_limit(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.chaos.time.monotonic", lambda: clock[0])
    fault = FaultState()
    request = FaultRequest(
        experiment_id=uuid4(),
        failure_type="application_latency",
        duration_seconds=10,
        namespace="sentinel-demo",
        latency_ms=750,
    )
    fault.start(request)
    assert fault.effect() == ("application_latency", 0.75)
    clock[0] = 110
    assert fault.effect() == (None, 0)
    assert fault.view()["active"] is False
    with pytest.raises(HTTPException):
        fault.cancel(uuid4())
