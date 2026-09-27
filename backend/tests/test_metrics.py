import httpx
from prometheus_client import generate_latest

from app.core.config import Settings
from app.main import create_app


async def test_metrics_contract_and_bounded_unknown_routes():
    application = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        await client.get("/health")
        await client.get("/missing/private-1")
        await client.get("/missing/private-2")
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert 'route="unmatched",status_code="404"} 2.0' in response.text
    assert "private-1" not in response.text
    assert "sentinel_http_request_duration_seconds_bucket" in response.text
    assert "sentinel_http_inflight_requests 0.0" in response.text


async def test_unhandled_error_counts_and_clears_inflight():
    application = create_app(Settings(_env_file=None))

    @application.get("/explode")
    async def explode():
        raise RuntimeError("test-only error")

    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/explode")).status_code == 500
    output = generate_latest(application.state.metrics_registry).decode()
    assert 'route="/explode",status_code="500"} 1.0' in output
    assert "sentinel_http_inflight_requests 0.0" in output
