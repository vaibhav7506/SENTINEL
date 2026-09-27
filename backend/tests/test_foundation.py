import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.main import create_app
from app.schemas.health import DependencyHealth, ReadinessResponse
from app.services.readiness import ReadinessChecker


def make_checker(
    *, extension="2.30.1", revision="0006_governed_proposals", db_error=False, status=200
):
    engine = MagicMock()
    connection = AsyncMock()
    connection.scalar.side_effect = [extension, revision]
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=None)
    if db_error:
        engine.connect.side_effect = RuntimeError("private-password")
    client = httpx.AsyncClient(
        base_url="http://prometheus:9090",
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text="ready")),
    )
    return ReadinessChecker(engine, client, 0.1), client


@pytest.mark.parametrize(
    "extension,revision,db_error,status,expected",
    [
        ("2.30.1", "0006_governed_proposals", False, 200, "ready"),
        (None, "0006_governed_proposals", False, 200, "not_ready"),
        ("2.30.1", "0001_timescaledb", False, 200, "not_ready"),
        ("2.30.1", None, False, 200, "not_ready"),
        ("2.30.1", "0006_governed_proposals", True, 200, "not_ready"),
        ("2.30.1", "0006_governed_proposals", False, 503, "not_ready"),
        ("2.30.1", "0006_governed_proposals", True, 503, "not_ready"),
    ],
)
async def test_dependency_checks(extension, revision, db_error, status, expected):
    checker, client = make_checker(
        extension=extension, revision=revision, db_error=db_error, status=status
    )
    async with client:
        result = await checker.check()
    assert result.status == expected
    assert set(result.dependencies) == {"database", "prometheus"}
    assert "private-password" not in result.model_dump_json()


async def test_prometheus_connection_failure():
    def fail(request):
        raise httpx.ConnectError("secret-in-url")

    checker, client = make_checker()
    await client.aclose()
    async with httpx.AsyncClient(
        base_url="http://prometheus", transport=httpx.MockTransport(fail)
    ) as failing:
        checker.client = failing
        result = await checker.check()
    assert result.dependencies["prometheus"].status == "down"
    assert "secret-in-url" not in result.model_dump_json()


async def test_checks_timeout():
    checker, client = make_checker()

    async def slow():
        await asyncio.sleep(1)

    checker.engine.connect.return_value.__aenter__ = slow
    async with client:
        assert (await checker.database()).status == "down"


@pytest.mark.parametrize("ready,status_code", [(True, 200), (False, 503)])
async def test_readiness_http_contract(ready, status_code):
    application = create_app(Settings(_env_file=None))
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        item = DependencyHealth(status="up" if ready else "down", detail="test dependency")
        application.state.readiness = AsyncMock()
        application.state.readiness.check.return_value = ReadinessResponse(
            status="ready" if ready else "not_ready",
            dependencies={"database": item, "prometheus": item},
        )
        response = await client.get("/ready")
        assert response.status_code == status_code
        assert response.json()["dependencies"]["database"]["status"] == item.status
        assert response.headers["X-Request-ID"]


async def test_liveness_does_not_require_dependencies():
    application = create_app(Settings(_env_file=None))
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        application.state.readiness = AsyncMock()
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        application.state.readiness.check.assert_not_called()


def test_configuration_hides_password_and_encodes_url():
    config = Settings(_env_file=None, postgres_password="secret@/:value")
    assert "secret@/:value" not in repr(config)
    assert config.database_url.password == "secret@/:value"
    assert config.database_url.drivername == "postgresql+psycopg"


@pytest.mark.parametrize(
    "changes", [{"postgres_port": 0}, {"readiness_timeout_seconds": 0}, {"environment": "unknown"}]
)
def test_invalid_configuration(changes):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **changes)


def test_json_logging_only_includes_safe_fields():
    record = logging.LogRecord("sentinel", logging.INFO, "", 0, "HTTP request", (), None)
    record.request_id = "test-id"
    record.password = "never-log-this"
    data = json.loads(JsonFormatter().format(record))
    assert data["request_id"] == "test-id"
    assert "password" not in data
    assert "never-log-this" not in json.dumps(data)
