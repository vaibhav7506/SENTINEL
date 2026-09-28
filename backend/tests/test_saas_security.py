"""Security contracts independent of database availability."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Settings
from app.saas.auth import RegistrationRequest, issue_session
from app.saas.operations import EnrollmentRequest, MetricRequest
from app.saas.security import normalized_email, password_hash, verify_password


def test_modern_password_hash_uses_random_salt_and_constant_time_verification():
    first, second = (
        password_hash("a-private-test-password"),
        password_hash("a-private-test-password"),
    )
    assert first != second and "a-private-test-password" not in first
    assert verify_password("a-private-test-password", first)
    assert not verify_password("incorrect-password", first)
    assert not verify_password("a-private-test-password", "corrupt")


def test_email_and_strict_request_schemas_do_not_accept_account_selection():
    assert normalized_email(" User@Example.COM ") == "user@example.com"
    for value in ("no-domain", "bad\n@example.com", "a@"):
        with pytest.raises(ValueError):
            normalized_email(value)
    with pytest.raises(ValidationError):
        RegistrationRequest(
            email="a@example.com", password="a" * 12, account_name="Demo", role="OWNER"
        )
    with pytest.raises(ValidationError):
        EnrollmentRequest(enrollment_token="enr_" + "a" * 40, hostname="host", account_id="chosen")
    with pytest.raises(ValidationError):
        MetricRequest(observed_at="2026-01-01T00:00:00Z", values={"unknown": 1})


async def test_production_session_cookie_is_host_scoped_secure_and_httponly():
    from uuid import uuid4

    settings = Settings(
        _env_file=None,
        environment="production",
        postgres_sslmode="require",
        integration_encryption_key=__import__("cryptography.fernet", fromlist=["Fernet"])
        .Fernet.generate_key()
        .decode(),
        saas_enabled=True,
        saas_public_api_url="https://sentinel.example.com/api",
        saas_allowed_origins=["https://sentinel.example.com"],
    )
    request = Request(
        {"type": "http", "app": SimpleNamespace(state=SimpleNamespace(settings=settings))}
    )
    response = Response()
    added = []
    await issue_session(
        request,
        response,
        SimpleNamespace(add=added.append),
        SimpleNamespace(account_id=uuid4(), id=uuid4()),
    )
    cookies = response.headers.getlist("set-cookie")
    assert "__Host-sentinel_session=" in cookies[0]
    assert "HttpOnly" in cookies[0] and "Secure" in cookies[0] and "SameSite=lax" in cookies[0]
    assert "Domain=" not in cookies[0] and "Path=/" in cookies[0]
    assert "__Host-sentinel_csrf=" in cookies[1] and "Secure" in cookies[1]
    assert len(added[0].token_hash) == 64 and len(added[0].csrf_hash) == 64
    assert response.headers["Cache-Control"] == "no-store"
