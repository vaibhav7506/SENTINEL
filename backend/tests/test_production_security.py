"""Public deployment must fail closed without credentials; local mode is preserved."""

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app

TOKEN = "test-only-production-read-token-123456789"


def test_production_requires_secret_and_forbids_chaos():
    for values in ({}, {"api_read_token": TOKEN, "chaos_enabled": True}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, environment="production", **values)


async def test_production_reads_require_bearer_and_chaos_has_separate_auth():
    app = create_app(
        Settings(
            _env_file=None,
            environment="production",
            api_read_token=TOKEN,
            postgres_sslmode="require",
        )
    )

    @app.get("/fixture")
    async def fixture():
        return {"safe": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for path in (
            "/fixture",
            "/console/snapshot",
            "/docs",
            "/openapi.json",
            "/chaos/failure-events",
        ):
            response = await client.get(path)
            assert response.status_code == 401
            assert TOKEN not in response.text
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/metrics")).status_code == 200
        assert (
            await client.get("/fixture", headers={"Authorization": "Bearer " + TOKEN})
        ).json() == {"safe": True}
        assert (
            await client.post("/chaos/experiments", headers={"Authorization": "Bearer " + TOKEN})
        ).status_code == 401


async def test_local_reads_still_use_original_contract():
    app = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/openapi.json")).status_code == 200
