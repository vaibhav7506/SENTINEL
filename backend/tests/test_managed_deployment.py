"""Managed deployment validation remains local until real cloud accounts exist."""

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings
from app.platform_metrics import render


def test_managed_url_ssl_and_secret_redaction():
    private = "postgres://tenant:fixture-private@db.example.com:5432/service?sslmode=verify-full"
    settings = Settings(_env_file=None, configured_database_url=private)
    assert settings.database_url.drivername == "postgresql+psycopg"
    assert settings.database_url.query["sslmode"] == "verify-full"
    assert "fixture-private" not in repr(settings)
    values = dict(
        environment="production",
        saas_enabled=True,
        saas_public_api_url="https://sentinel.example.com/api",
        saas_allowed_origins=["https://sentinel.example.com"],
        integration_encryption_key=Fernet.generate_key().decode(),
    )
    assert (
        Settings(_env_file=None, configured_database_url=private, **values).database_url.host
        == "db.example.com"
    )
    with pytest.raises(ValidationError):
        _ = Settings(
            _env_file=None,
            configured_database_url=private.replace("verify-full", "disable"),
            **values,
        )
    with pytest.raises(ValueError, match="Invalid PostgreSQL"):
        _ = Settings(
            _env_file=None, configured_database_url="https://user:fixture-private@bad.example/path"
        ).database_url


async def test_platform_export_is_fixed_and_fails_safely():
    class AggregateRedis:
        async def mget(self, keys):
            assert all(key.startswith("sentinel:platform:") for key in keys)
            return ["2"] * len(keys)

        async def llen(self, key):
            assert key == "sentinel"
            return 3

    exported = (await render(AggregateRedis())).decode()
    assert "queue_depth 3" in exported and 'bucket{le="+Inf"} 2.0' in exported
    assert (
        "account_id" not in exported and "host_id" not in exported and "cpu_usage" not in exported
    )

    class Unavailable:
        async def mget(self, keys):
            raise RuntimeError("fixture-private")

    assert (await render(Unavailable())).decode().endswith("redis_up 0\n")
