from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.main import create_app
from app.models import Host
from app.saas.ingestion import Batch, heartbeat
from app.saas.integrations import decrypt_destination, encrypt_destination


def test_hardware_bounds_schema_and_configured_heartbeats():
    settings = Settings(agent_stale_intervals=3, agent_offline_intervals=8)
    now = datetime.now(UTC)
    host = Host(status="active", reporting_interval_seconds=60, last_seen=now)
    assert heartbeat(host, settings, now + timedelta(seconds=180)) == "Online"
    assert heartbeat(host, settings, now + timedelta(seconds=181)) == "Stale"
    assert heartbeat(host, settings, now + timedelta(seconds=481)) == "Offline"
    for values in ({"cpu_usage_percent": 101}, {"error_rate": float("nan")}, {"pid_1": 5}):
        with pytest.raises(ValidationError):
            Batch.model_validate_json(
                __import__("json").dumps(
                    {
                        "schema_version": 1,
                        "samples": [{"timestamp": now.isoformat(), "values": values}],
                    }
                )
            )


def test_encryption_is_bound_to_account_and_hides_plaintext():
    settings = Settings(integration_encryption_key=SecretStr(Fernet.generate_key().decode()))
    account = uuid4()
    secret = "https://hooks.slack.com/services/fixture-only-secret"
    encrypted = encrypt_destination(secret, account, settings)
    assert secret not in encrypted and encrypted.startswith("enc:v1:")
    assert decrypt_destination(encrypted, account, settings) == secret
    with pytest.raises(ValueError):
        decrypt_destination(encrypted, uuid4(), settings)
    with pytest.raises((ValueError, InvalidToken)):
        decrypt_destination(encrypted + "broken", account, settings)
    with pytest.raises(ValueError):
        decrypt_destination(secret, account, settings)


@pytest.mark.asyncio
async def test_authentication_precedes_payload_parsing():
    app = create_app(Settings(saas_enabled=True))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post("/agent/v1/metrics", content=b"invalid" * 50000)
            assert response.status_code == 401
            assert app.state.ingestion_rejections.labels("credential")._value.get() == 1
