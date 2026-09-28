"""Adversarial contracts against real PostgreSQL/Timescale, never production data."""

import asyncio
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.main import create_app
from app.models import (
    Alert,
    ChaosExperiment,
    FeatureWindow,
    Host,
    Incident,
    ModelVersion,
    Prediction,
    RemediationProposal,
)
from app.saas.models import (
    TENANT_TABLES,
    AgentCredential,
    AlertChannel,
    AuditEvent,
    AuthSession,
    EnrollmentToken,
    User,
)
from app.saas.repository import account_session
from app.saas.security import digest

pytestmark = pytest.mark.skipif(
    os.environ.get("SENTINEL_TEST_DATABASE") != "1", reason="requires disposable Postgres"
)


@pytest.fixture
async def tenancy():
    settings = Settings(
        _env_file=None, saas_enabled=True, saas_allowed_origins=["http://testserver"]
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://testserver") as a,
            httpx.AsyncClient(transport=transport, base_url="http://testserver") as b,
        ):
            identities = []
            for client in (a, b):
                password = secrets.token_urlsafe(24)
                email = uuid4().hex + "@isolated.test"
                response = await client.post(
                    "/auth/register",
                    json={
                        "email": email,
                        "password": password,
                        "account_name": "Isolation fixture",
                    },
                )
                assert response.status_code == 201, response.text
                identities.append((response.json(), email, password))
            yield app, a, b, identities


def csrf(client):
    return {"X-CSRF-Token": client.cookies["sentinel_csrf"]}


async def enroll_host(client, name="isolated-host"):
    token = await client.post("/hosts/enrollment-tokens", json={}, headers=csrf(client))
    assert token.status_code == 201, token.text
    payload = {"enrollment_token": token.json()["token"], "hostname": name}
    response = await client.post("/agent/enroll", json=payload)
    assert response.status_code == 201, response.text
    return token.json(), response.json(), payload


async def seeded(app, client, account_id):
    _, identity, _ = await enroll_host(client)
    now = datetime.now(UTC)
    async with async_sessionmaker(app.state.engine, expire_on_commit=False)() as db:
        model = ModelVersion(
            version="isolation-contract-" + uuid4().hex,
            schema_version="fixture",
            schema_hash="a" * 64,
            artifact_uri="fixture-only",
            threshold=0.8,
            training_metadata={},
        )
        db.add(model)
        await db.flush()
        prediction = Prediction(
            host_id=identity["host_id"],
            model_version_id=model.id,
            predicted_at=now,
            feature_window_end=now,
            schema_version="fixture",
            probability=0.9,
            anomaly_score=0.1,
            horizon_seconds=600,
            explanation={},
        )
        experiment = ChaosExperiment(
            host_id=identity["host_id"],
            environment="test",
            failure_type="fixture",
            started_at=now,
            status="completed",
            parameters={},
            expected_degradation={},
        )
        feature = FeatureWindow(
            host_id=identity["host_id"],
            window_start=now - timedelta(seconds=900),
            window_end=now,
            schema_version="fixture",
            schema_hash="a" * 64,
            feature_names=["cpu"],
            feature_values=[1.0],
            features={"cpu": 1.0},
            quality={},
        )
        db.add_all([prediction, experiment, feature])
        await db.flush()
        incident = Incident(
            host_id=identity["host_id"],
            model_version_id=model.id,
            prediction_id=prediction.id,
            status="open",
            summary={},
        )
        db.add(incident)
        await db.flush()
        alert = Alert(incident_id=incident.id, channel="generic", status="pending", payload={})
        proposal = RemediationProposal(
            incident_id=incident.id,
            prediction_id=prediction.id,
            runbook_reference="fixture",
            parameters={},
        )
        channel = AlertChannel(
            account_id=UUID(account_id),
            name="Fixture receiver",
            kind="generic",
            destination="https://example.invalid/private-webhook",
        )
        db.add_all([alert, proposal, channel])
        await db.commit()
        return identity, {
            "predictions": prediction.id,
            "incidents": incident.id,
            "alerts": alert.id,
            "experiments": experiment.id,
            "remediation-proposals": proposal.id,
            "alert-channels": channel.id,
            "agent-credentials": UUID(identity["credential_id"]),
        }


async def test_authentication_csrf_logout_and_password_storage(tenancy):
    app, a, _, identities = tenancy
    user, email, password = identities[0]
    assert (await a.get("/auth/me")).json()["account"]["id"] == user["account"]["id"]
    assert (await a.post("/hosts/enrollment-tokens", json={})).status_code == 403
    assert (
        await a.post(
            "/hosts/enrollment-tokens",
            json={},
            headers={**csrf(a), "Origin": "https://evil.invalid"},
        )
    ).status_code == 403
    cookies = httpx.Cookies(a.cookies)
    assert (await a.post("/auth/logout", headers=csrf(a))).status_code == 204
    assert (await a.get("/auth/me")).status_code == 401
    a.cookies.update(cookies)
    assert (await a.get("/auth/me")).status_code == 401
    login = await a.post("/auth/login", json={"email": email.upper(), "password": password})
    assert login.status_code == 200
    assert "HttpOnly" in login.headers.get_list("set-cookie")[0]
    assert "SameSite=lax" in login.headers.get_list("set-cookie")[0]
    async with async_sessionmaker(app.state.engine)() as db:
        row = await db.get(User, UUID(user["id"]))
        assert row.password_hash.startswith("scrypt$") and password not in row.password_hash
        sessions = (
            await db.scalars(select(AuthSession).where(AuthSession.user_id == row.id))
        ).all()
        assert all(s.token_hash != a.cookies["sentinel_session"] for s in sessions)


async def test_one_time_enrollment_metrics_hashes_and_individual_revocation(tenancy):
    app, a, _, _ = tenancy
    token, first, payload = await enroll_host(a)
    _, second, _ = await enroll_host(a)
    assert first["host_id"] != second["host_id"] and first["credential"] != second["credential"]
    assert (await a.post("/agent/enroll", json=payload)).status_code == 401
    assert (
        await a.post("/agent/enroll", json={**payload, "account_id": str(uuid4())})
    ).status_code == 422
    async with async_sessionmaker(app.state.engine)() as db:
        stored = await db.get(AgentCredential, UUID(first["credential_id"]))
        assert (
            stored.key_hash == digest(first["credential"])
            and stored.key_hash != first["credential"]
        )
        enrollment = await db.get(EnrollmentToken, UUID(token["id"]))
        assert enrollment.token_hash == digest(token["token"]) and enrollment.consumed_at
    sample = {"observed_at": datetime.now(UTC).isoformat(), "values": {"cpu_usage_percent": 12.5}}
    header = {"Authorization": "Bearer " + first["credential"]}
    assert (await a.post("/agent/metrics", json=sample, headers=header)).status_code == 204
    assert (
        await a.post(
            "/agent/metrics", json={**sample, "host_id": second["host_id"]}, headers=header
        )
    ).status_code == 422
    metrics = await a.get(f"/hosts/{first['host_id']}/metrics")
    assert metrics.json()[0]["values"]["cpu_usage_percent"] == 12.5
    listed = await a.get(f"/hosts/{first['host_id']}/credentials")
    assert first["credential"] not in listed.text and "key_hash" not in listed.text
    assert (
        await a.post(f"/agent-credentials/{first['credential_id']}/revoke", headers=csrf(a))
    ).status_code == 204
    assert (await a.post("/agent/metrics", json=sample, headers=header)).status_code == 401
    assert (
        await a.post(
            "/agent/metrics",
            json=sample,
            headers={"Authorization": "Bearer " + second["credential"]},
        )
    ).status_code == 204


async def test_cross_account_api_reads_and_writes(tenancy):
    app, a, b, identities = tenancy
    a_host, _ = await seeded(app, a, identities[0][0]["account"]["id"])
    b_host, resources = await seeded(app, b, identities[1][0]["account"]["id"])
    sample = {"observed_at": datetime.now(UTC).isoformat(), "values": {"cpu_usage_percent": 71.0}}
    assert (
        await b.post(
            "/agent/metrics",
            json=sample,
            headers={"Authorization": "Bearer " + b_host["credential"]},
        )
    ).status_code == 204
    for path in [
        f"/hosts/{b_host['host_id']}",
        f"/console/hosts/{b_host['host_id']}",
        *[f"/hosts/{b_host['host_id']}/{part}" for part in ("metrics", "features", "credentials")],
        *[f"/resources/{name}/{identifier}" for name, identifier in resources.items()],
    ]:
        assert (await a.get(path)).status_code == 404, path
    for path in (
        "/hosts",
        "/predictions",
        "/incidents",
        "/alerts",
        "/chaos/experiments",
        "/alert-channels",
        "/remediation/proposals",
        "/console/snapshot",
    ):
        response = await a.get(path)
        assert response.status_code == 200, (path, response.text)
        assert b_host["host_id"] not in response.text, path
        assert all(str(identifier) not in response.text for identifier in resources.values()), path
    for path in [
        f"/agent-credentials/{b_host['credential_id']}/revoke",
        f"/alert-channels/{resources['alert-channels']}/disable",
        f"/remediation/proposals/{resources['remediation-proposals']}/approve",
        f"/chaos/experiments/{resources['experiments']}/cancel",
    ]:
        assert (await a.post(path, headers=csrf(a))).status_code == 404, path
    response = await a.patch(
        f"/team/{identities[1][0]['id']}", json={"role": "ADMIN"}, headers=csrf(a)
    )
    assert response.status_code == 404
    assert (await a.get(f"/hosts/{a_host['host_id']}")).status_code == 200


async def test_rls_blocks_raw_queries_and_cross_tenant_updates(tenancy):
    app, a, b, identities = tenancy
    account_a = UUID(identities[0][0]["account"]["id"])
    account_b = UUID(identities[1][0]["account"]["id"])
    await seeded(app, a, str(account_a))
    b_host, _ = await seeded(app, b, str(account_b))
    async with account_session(app.state.engine, account_a) as db:
        role = (
            await db.execute(
                text(
                    "SELECT current_user,rolsuper,rolbypassrls FROM pg_roles "
                    "WHERE rolname=current_user"
                )
            )
        ).one()
        assert role == ("sentinel_app", False, False)
        for table in (*TENANT_TABLES, "accounts"):
            column = "id" if table == "accounts" else "account_id"
            assert (
                await db.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE {column}=:account"),
                    {"account": account_b},
                )
                == 0
            ), table
            if table != "audit_events":
                result = await db.execute(
                    text(f"UPDATE {table} SET {column}={column} WHERE {column}=:account"),
                    {"account": account_b},
                )
                assert result.rowcount == 0, table
        await db.commit()
        # Pool reuse and new transactions must reapply the low privilege role and context.
        assert (
            await db.scalar(
                text("SELECT count(*) FROM hosts WHERE id=:host"), {"host": b_host["host_id"]}
            )
            == 0
        )
    async with account_session(app.state.engine, account_a) as db:
        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    "INSERT INTO metrics(host_id,observed_at,values,account_id) "
                    "VALUES (:host,now(),'{}',:account)"
                ),
                {"host": b_host["host_id"], "account": account_a},
            )
    async with app.state.engine.begin() as connection:
        await connection.execute(text("SET LOCAL ROLE sentinel_app"))
        assert await connection.scalar(text("SELECT count(*) FROM hosts")) == 0


async def test_member_and_admin_roles_and_immutable_audit(tenancy):
    app, a, _, identities = tenancy
    for role in ("MEMBER", "ADMIN"):
        email, password = uuid4().hex + "@isolated.test", secrets.token_urlsafe(24)
        created = await a.post(
            "/team", headers=csrf(a), json={"email": email, "password": password, "role": role}
        )
        assert created.status_code == 201, created.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as member:
            assert (
                await member.post("/auth/login", json={"email": email, "password": password})
            ).status_code == 200
            assert (await member.get("/hosts")).status_code == 200
            for path in ("/team", "/alert-channels"):
                assert (await member.get(path)).status_code == 403
            token = await member.post("/hosts/enrollment-tokens", json={}, headers=csrf(member))
            assert token.status_code == (403 if role == "MEMBER" else 201)
            assert (
                await member.patch("/account", json={"name": "attack"}, headers=csrf(member))
            ).status_code == 403
            if role == "MEMBER":
                assert (
                    await member.post("/chaos/experiments", json={}, headers=csrf(member))
                ).status_code == 403
    assert (
        await a.patch(f"/team/{identities[0][0]['id']}", json={"role": "MEMBER"}, headers=csrf(a))
    ).status_code == 409
    account_id = UUID(identities[0][0]["account"]["id"])
    async with async_sessionmaker(app.state.engine)() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.account_id == account_id)
            )
            >= 4
        )
        with pytest.raises(DBAPIError):
            await db.execute(
                text("UPDATE audit_events SET action='tampered' WHERE account_id=:account"),
                {"account": account_id},
            )


async def test_revoked_and_expired_enrollment_tokens(tenancy):
    app, a, _, _ = tenancy
    for expired in (True, False):
        response = await a.post("/hosts/enrollment-tokens", json={}, headers=csrf(a))
        value = response.json()
        if expired:
            async with async_sessionmaker(app.state.engine)() as db, db.begin():
                row = await db.get(EnrollmentToken, UUID(value["id"]))
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            assert (
                await a.post(f"/hosts/enrollment-tokens/{value['id']}/revoke", headers=csrf(a))
            ).status_code == 204
        assert (
            await a.post(
                "/agent/enroll", json={"enrollment_token": value["token"], "hostname": "rejected"}
            )
        ).status_code == 401


async def test_concurrent_enrollment_has_exactly_one_winner(tenancy):
    app, a, _, _ = tenancy
    token = (await a.post("/hosts/enrollment-tokens", json={}, headers=csrf(a))).json()
    hostname = "concurrent-fixture-" + uuid4().hex
    payload = {"enrollment_token": token["token"], "hostname": hostname}
    results = await asyncio.gather(
        a.post("/agent/enroll", json=payload), a.post("/agent/enroll", json=payload)
    )
    assert sorted(r.status_code for r in results) == [201, 401]
    async with async_sessionmaker(app.state.engine)() as db:
        assert (
            await db.scalar(select(func.count()).select_from(Host).where(Host.hostname == hostname))
            == 1
        )


async def test_approved_proposal_is_audited_without_execution_and_channel_is_private(tenancy):
    app, a, _, identities = tenancy
    account = identities[0][0]["account"]["id"]
    _, resources = await seeded(app, a, account)
    identifier = resources["remediation-proposals"]
    result = await a.post(f"/remediation/proposals/{identifier}/approve", headers=csrf(a))
    assert result.status_code == 200, result.text
    assert result.json()["approval_status"] == "approved"
    assert result.json()["execution_status"] == "not_started"
    assert result.json()["execution_requested"] is False
    assert (
        await a.post(f"/remediation/proposals/{identifier}/approve", headers=csrf(a))
    ).status_code == 200
    async with async_sessionmaker(app.state.engine)() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.account_id == UUID(account),
                    AuditEvent.action == "remediation.approved",
                )
            )
            == 1
        )
    channel = await a.post(
        "/alert-channels",
        headers=csrf(a),
        json={
            "name": "Private receiver",
            "kind": "generic",
            "destination": "http://127.0.0.1:8091/secret-path",
        },
    )
    assert channel.status_code == 201
    assert (
        "destination" not in channel.json()
        and "secret-path" not in (await a.get("/alert-channels")).text
    )
    forbidden = await a.post(
        "/alert-channels",
        headers=csrf(a),
        json={
            "name": "Unapproved receiver",
            "kind": "generic",
            "destination": "https://internal.invalid/path",
        },
    )
    assert forbidden.status_code == 422
