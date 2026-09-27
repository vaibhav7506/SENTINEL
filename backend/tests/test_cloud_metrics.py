"""Managed Prometheus credentials and query paths must stay bounded and private."""

import base64
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.prometheus import create_prometheus_client
from app.services.readiness import ReadinessChecker

TOKEN = "test-only-metrics-read-token"


def settings(**values):
    return Settings(
        _env_file=None,
        prometheus_url="https://metrics.example.test/api/prom",
        prometheus_username="12345",
        prometheus_password=TOKEN,
        prometheus_readiness_mode="query",
        **values,
    )


@pytest.mark.parametrize(
    "values",
    [
        {
            "prometheus_url": "http://metrics.example.test",
            "prometheus_username": "12345",
            "prometheus_password": TOKEN,
        },
        {"prometheus_username": "12345"},
        {"prometheus_password": TOKEN},
        {"prometheus_url": "https://user:password@metrics.example.test"},
        {"prometheus_url": "https://metrics.example.test?token=private"},
        {"prometheus_url": "https://metrics.example.test#private"},
    ],
)
def test_invalid_cloud_auth_fails_without_exposing_values(values):
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)
    assert TOKEN not in str(caught.value)
    assert "user:password" not in str(caught.value)
    assert "token=private" not in str(caught.value)


async def test_query_prefix_and_basic_auth_are_preserved_without_following_redirects():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example.test/"})

    config = settings()
    assert TOKEN not in repr(config)
    async with create_prometheus_client(
        config, 1, transport=httpx.MockTransport(respond)
    ) as client:
        response = await client.get("/api/v1/query", params={"query": "up"})
    assert response.status_code == 302
    assert len(seen) == 1
    assert seen[0].url.path == "/api/prom/api/v1/query"
    assert seen[0].headers["Authorization"] == (
        "Basic " + base64.b64encode(f"12345:{TOKEN}".encode()).decode()
    )
    assert TOKEN not in str(seen[0].url)


@pytest.mark.parametrize("mode", ["success", "unauthorized", "malformed", "error", "empty"])
async def test_cloud_readiness_requires_a_successful_query(mode):
    seen = []

    def respond(request):
        seen.append(request)
        if mode == "unauthorized":
            return httpx.Response(401, text=TOKEN)
        if mode == "malformed":
            return httpx.Response(200, text="not a metrics response")
        return httpx.Response(
            200,
            json={
                "status": "error" if mode == "error" else "success",
                "data": {
                    "resultType": "vector",
                    "result": [] if mode == "empty" else [{"metric": {}, "value": [1, "1"]}],
                },
            },
        )

    async with create_prometheus_client(
        settings(), 1, transport=httpx.MockTransport(respond)
    ) as client:
        health = await ReadinessChecker(MagicMock(), client, 1, "query").prometheus()
    assert health.status == ("up" if mode == "success" else "down")
    assert seen[0].url.path == "/api/prom/api/v1/query"
    assert seen[0].url.params["query"] == "vector(1)"
    assert TOKEN not in health.model_dump_json()
