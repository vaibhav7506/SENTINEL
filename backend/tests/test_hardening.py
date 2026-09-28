"""Boundary tests use local ASGI and transport fixtures, never external destinations."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.main import create_app


async def test_api_work_has_a_deadline_and_clears_inflight():
    app = create_app(Settings(_env_file=None))

    @app.get("/fixture-slow")
    async def slow():
        await asyncio.sleep(11)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/fixture-slow")
        assert response.status_code == 504
        metrics = (await client.get("/metrics")).text
        assert 'status_code="504"' in metrics
        assert "sentinel_http_inflight_requests 0.0" in metrics


@pytest.mark.parametrize(
    "started,expires",
    [
        ("2026-01-01T00:00:00", "2026-01-01T00:00:30"),
        ("2026-01-01T01:00:00Z", "2026-01-01T01:00:30Z"),
        ("2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"),
        ("2026-01-01T00:00:00Z", "2025-12-31T23:59:59Z"),
    ],
)
def test_injector_rejects_unbounded_or_untrustworthy_timing(started, expires):
    from datetime import UTC, datetime

    from app.api.chaos import injector_times

    with pytest.raises(ValueError):
        injector_times(
            {"started_at": started, "expires_at": expires}, datetime(2026, 1, 1, tzinfo=UTC), 30
        )


async def test_unhandled_database_error_is_safe_and_liveness_survives(caplog):
    app = create_app(Settings(_env_file=None))

    @app.get("/fixture-failure")
    async def fail():
        raise SQLAlchemyError("postgres://password-must-not-leak")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/fixture-failure")
        assert response.status_code == 503
        assert response.json() == {"detail": "Service unavailable"}
        assert response.headers["X-Request-ID"]
        assert (await client.get("/health")).status_code == 200
    assert "password-must-not-leak" not in caplog.text


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}])
async def test_body_limit_even_without_honest_content_length(headers):
    app = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post("/chaos/experiments", content=b"x" * 16385, headers=headers)
    assert response.status_code == 413


async def test_chaos_auth_precedes_validation_and_errors_omit_input():
    app = create_app(Settings(_env_file=None, chaos_control_token="a" * 32))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (
            await client.post("/chaos/experiments", json={"secret": "private"})
        ).status_code == 401
        response = await client.post(
            "/chaos/experiments",
            json={"secret": "private"},
            headers={"Authorization": "Bearer " + "a" * 32},
        )
    assert response.status_code == 422 and "private" not in response.text


async def test_experiment_rate_limit_is_bounded_and_cancel_stays_available():
    app = create_app(Settings(_env_file=None, chaos_control_token="a" * 32))
    headers = {"Authorization": "Bearer " + "a" * 32}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for _ in range(6):
            assert (
                await client.post("/chaos/experiments", json={}, headers=headers)
            ).status_code == 422
        response = await client.post("/chaos/experiments", json={}, headers=headers)
        assert response.status_code == 429 and response.headers["Retry-After"] == "60"
        assert (
            await client.post("/chaos/experiments/invalid/cancel", headers=headers)
        ).status_code == 422


async def test_uri_bounds_and_closed_cors(caplog):
    app = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/" + "private-untrusted-path" * 110)
        assert response.status_code == 414
        assert "private-untrusted-path" not in caplog.text
        response = await client.options(
            "/chaos/experiments",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in response.headers


async def test_corrupt_experiment_cannot_reach_injector(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    import app.api.chaos as chaos

    app = create_app(Settings(_env_file=None, chaos_control_token="a" * 32))
    app.state.engine = object()
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.scalar.return_value = SimpleNamespace(parameters={"duration_seconds": "corrupt"})

    monkeypatch.setattr(chaos, "request_session", lambda request: session)
    injector = AsyncMock()
    monkeypatch.setattr(chaos.httpx, "AsyncClient", injector)
    # Create the ASGI client before patching the shared httpx module's class.
    async with httpx._client.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/chaos/experiments/{uuid4()}/cancel",
            headers={
                "Authorization": "Bearer " + "a" * 32,
            },
        )
    assert response.status_code == 409
    injector.assert_not_called()
