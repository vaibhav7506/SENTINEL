"""Account-scoped management and one-time agent enrollment."""

import asyncio
import math
import secrets
import shlex
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    Alert,
    ChaosExperiment,
    FeatureWindow,
    Host,
    Incident,
    Prediction,
    RemediationProposal,
)
from app.saas.auth import LoginRequest, audit
from app.saas.integrations import encrypt_destination
from app.saas.models import (
    Account,
    AgentCredential,
    AlertChannel,
    AuditEvent,
    EnrollmentToken,
    MetricSample,
    User,
)
from app.saas.repository import get_for_account, request_session
from app.saas.security import digest, password_hash, raw_secret, require_role

router = APIRouter(tags=["tenant operations and enrollment"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenRequest(StrictRequest):
    expires_in_seconds: int = Field(default=600, ge=60, le=3600)


class EnrollmentRequest(StrictRequest):
    enrollment_token: str = Field(min_length=32, max_length=128)
    hostname: str = Field(min_length=1, max_length=256)
    display_name: str = Field(default="", max_length=256)
    agent_version: str = Field(default="", max_length=64)
    platform: str = Field(default="", max_length=64)
    architecture: str = Field(default="", max_length=64)


class MetricRequest(StrictRequest):
    observed_at: datetime
    values: dict[str, float] = Field(min_length=1, max_length=32)

    @field_validator("values")
    @classmethod
    def real_values(cls, value: dict[str, float]) -> dict[str, float]:
        from app.features.schema import HOST_METRICS

        allowed = set(HOST_METRICS) | {
            "memory_capacity_bytes",
            "disk_capacity_bytes",
            "cpu_count",
            "sample_timestamp_seconds",
            "sample_success",
        }
        if not set(value) <= allowed or any(not math.isfinite(v) for v in value.values()):
            raise ValueError("Invalid metric names or values")
        return value


class TeamRequest(LoginRequest):
    role: Literal["OWNER", "ADMIN", "MEMBER"] = "MEMBER"


class RoleRequest(StrictRequest):
    role: Literal["OWNER", "ADMIN", "MEMBER"]


class AccountRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=160)


class ChannelRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["generic", "slack", "email"]
    destination: str = Field(min_length=1, max_length=2048)


def host_view(host: Host, settings: Any = None) -> dict[str, Any]:
    from app.core.config import Settings
    from app.saas.ingestion import heartbeat

    return {
        "id": host.id,
        "account_id": str(host.account_id),
        "display_name": host.display_name or host.name,
        "hostname": host.hostname,
        "agent_version": host.agent_version,
        "platform": host.platform,
        "architecture": host.architecture,
        "first_seen_at": host.first_seen,
        "last_seen_at": host.last_seen,
        "status": heartbeat(host, settings or Settings()),
        "reporting_interval_seconds": host.reporting_interval_seconds,
        "capacity": host.capacity,
        "created_at": host.created_at,
    }


def person_view(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


@router.get("/hosts")
async def hosts(request: Request) -> list[dict[str, Any]]:
    async with request_session(request) as db:
        return [
            host_view(h, request.app.state.settings)
            for h in (
                await db.scalars(select(Host).order_by(Host.created_at.desc()).limit(100))
            ).all()
        ]


@router.get("/hosts/{host_id}")
async def host(host_id: str, request: Request) -> dict[str, Any]:
    async with request_session(request) as db:
        h = await get_for_account(db, Host, host_id)
        if h is None:
            raise HTTPException(404, "Host not found")
        return host_view(h, request.app.state.settings)


@router.post("/hosts/enrollment-tokens", status_code=201)
async def token(payload: TokenRequest, request: Request) -> dict[str, Any]:
    p = require_role(request, "OWNER", "ADMIN")
    raw = raw_secret("enr_")
    now = datetime.now(UTC)
    async with request_session(request) as db:
        row = EnrollmentToken(
            account_id=p.account_id,
            token_hash=digest(raw),
            prefix=raw[:12],
            expires_at=now + timedelta(seconds=payload.expires_in_seconds),
        )
        db.add(row)
        await db.flush()
        audit(db, p.account_id, "enrollment.issued", row.id, p.user_id)
        await db.commit()
        base = str(request.app.state.settings.saas_public_api_url).rstrip("/")
        command = (
            f"curl -fsSL {shlex.quote(base + '/agent/install.sh')} | sudo bash -s -- "
            f"--server {shlex.quote(base)} --enrollment-token {shlex.quote(raw)}"
        )
        return {
            "id": str(row.id),
            "token": raw,
            "expires_at": row.expires_at,
            "install_command": command,
            "displayed_once": True,
        }


@router.get("/hosts/enrollment-tokens/issued")
async def issued_tokens(request: Request) -> list[dict[str, Any]]:
    require_role(request, "OWNER", "ADMIN")
    async with request_session(request) as db:
        return [
            {
                "id": str(r.id),
                "prefix": r.prefix,
                "expires_at": r.expires_at,
                "consumed_at": r.consumed_at,
                "revoked_at": r.revoked_at,
            }
            for r in (
                await db.scalars(
                    select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc()).limit(100)
                )
            ).all()
        ]


@router.post("/hosts/enrollment-tokens/{token_id}/revoke", status_code=204)
async def revoke_token(token_id: UUID, request: Request) -> None:
    p = require_role(request, "OWNER", "ADMIN")
    async with request_session(request) as db:
        row = await get_for_account(db, EnrollmentToken, token_id)
        if row is None:
            raise HTTPException(404, "Enrollment token not found")
        row.revoked_at = datetime.now(UTC)
        audit(db, p.account_id, "enrollment.revoked", row.id, p.user_id)
        await db.commit()


@router.post("/agent/enroll", status_code=201)
async def enroll(payload: EnrollmentRequest, request: Request) -> dict[str, Any]:
    # Ownership comes from this token. The schema rejects client account and host IDs.
    async with async_sessionmaker(request.app.state.engine, expire_on_commit=False)() as db:
        row = await db.scalar(
            select(EnrollmentToken)
            .where(EnrollmentToken.token_hash == digest(payload.enrollment_token))
            .with_for_update()
        )
        now = datetime.now(UTC)
        if row is None or row.revoked_at or row.consumed_at or row.expires_at <= now:
            raise HTTPException(401, "Enrollment token invalid or unavailable")
        h = Host(
            id=str(uuid4()),
            account_id=row.account_id,
            name=payload.display_name or payload.hostname,
            display_name=payload.display_name or payload.hostname,
            hostname=payload.hostname,
            agent_version=payload.agent_version,
            platform=payload.platform,
            architecture=payload.architecture,
            environment=request.app.state.settings.environment,
            first_seen=now,
            last_seen=now,
            status="active",
            reporting_interval_seconds=request.app.state.settings.agent_reporting_interval_seconds,
        )
        db.add(h)
        await db.flush()
        key = raw_secret("sk_host_" + secrets.token_hex(4) + "_")
        credential = AgentCredential(
            account_id=row.account_id, host_id=h.id, key_prefix=key[:17], key_hash=digest(key)
        )
        db.add(credential)
        row.consumed_at = now
        audit(db, row.account_id, "agent.enrolled", h.id)
        await db.commit()
        return {
            "host_id": h.id,
            "credential_id": str(credential.id),
            "credential": key,
            "displayed_once": True,
        }


@router.post("/agent/metrics", status_code=204)
async def agent_metrics(payload: MetricRequest, request: Request) -> None:
    # Compatibility endpoint shares authentication, bounds and distributed limits.
    import json

    from app.saas.ingestion import ingest

    request._body = json.dumps(
        {
            "schema_version": 1,
            "samples": [{"timestamp": payload.observed_at.isoformat(), "values": payload.values}],
        }
    ).encode()
    await ingest(request)


@router.get("/hosts/{host_id}/metrics")
async def metrics(host_id: str, request: Request) -> list[dict[str, Any]]:
    async with request_session(request) as db:
        if await get_for_account(db, Host, host_id) is None:
            raise HTTPException(404, "Host not found")
        return [
            {"host_id": r.host_id, "observed_at": r.observed_at, "values": r.values}
            for r in (
                await db.scalars(
                    select(MetricSample)
                    .where(MetricSample.host_id == host_id)
                    .order_by(MetricSample.observed_at.desc())
                    .limit(100)
                )
            ).all()
        ]


@router.get("/hosts/{host_id}/features")
async def features(host_id: str, request: Request) -> list[dict[str, Any]]:
    async with request_session(request) as db:
        if await get_for_account(db, Host, host_id) is None:
            raise HTTPException(404, "Host not found")
        return [
            {
                "host_id": r.host_id,
                "window_end": r.window_end,
                "schema_version": r.schema_version,
                "features": r.features,
                "quality": r.quality,
            }
            for r in (
                await db.scalars(
                    select(FeatureWindow)
                    .where(FeatureWindow.host_id == host_id)
                    .order_by(FeatureWindow.window_end.desc())
                    .limit(30)
                )
            ).all()
        ]


@router.get("/hosts/{host_id}/credentials")
async def credentials(host_id: str, request: Request) -> list[dict[str, Any]]:
    require_role(request, "OWNER", "ADMIN")
    async with request_session(request) as db:
        if await get_for_account(db, Host, host_id) is None:
            raise HTTPException(404, "Host not found")
        return [
            {
                "id": str(r.id),
                "host_id": r.host_id,
                "key_prefix": r.key_prefix,
                "created_at": r.created_at,
                "last_used_at": r.last_used_at,
                "revoked_at": r.revoked_at,
            }
            for r in (
                await db.scalars(select(AgentCredential).where(AgentCredential.host_id == host_id))
            ).all()
        ]


@router.post("/agent-credentials/{key_id}/revoke", status_code=204)
async def revoke_credential(key_id: UUID, request: Request) -> None:
    p = require_role(request, "OWNER", "ADMIN")
    async with request_session(request) as db:
        row = await get_for_account(db, AgentCredential, key_id)
        if row is None:
            raise HTTPException(404, "Credential not found")
        row.revoked_at = datetime.now(UTC)
        audit(db, p.account_id, "agent.credential_revoked", row.id, p.user_id)
        await db.commit()


@router.get("/team")
async def team(request: Request) -> list[dict[str, Any]]:
    require_role(request, "OWNER")
    async with request_session(request) as db:
        return [
            person_view(u)
            for u in (await db.scalars(select(User).order_by(User.created_at).limit(100))).all()
        ]


@router.post("/team", status_code=201)
async def add_user(payload: TeamRequest, request: Request) -> dict[str, Any]:
    p = require_role(request, "OWNER")
    async with request.app.state.auth_hash_slots:
        encoded = await asyncio.to_thread(password_hash, payload.password)
    async with request_session(request) as db:
        u = User(
            account_id=p.account_id, email=payload.email, password_hash=encoded, role=payload.role
        )
        db.add(u)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "User could not be created") from None
        audit(db, p.account_id, "user.created", u.id, p.user_id)
        await db.commit()
        return person_view(u)


@router.patch("/team/{user_id}")
async def change_role(user_id: UUID, payload: RoleRequest, request: Request) -> dict[str, Any]:
    p = require_role(request, "OWNER")
    async with request_session(request) as db:
        await db.scalar(select(Account).where(Account.id == p.account_id).with_for_update())
        u = await get_for_account(db, User, user_id)
        if u is None:
            raise HTTPException(404, "User not found")
        owners = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "OWNER", User.is_active.is_(True))
        )
        if u.role == "OWNER" and payload.role != "OWNER" and owners == 1:
            raise HTTPException(409, "The account must retain an owner")
        u.role = payload.role
        u.updated_at = datetime.now(UTC)
        audit(db, p.account_id, "user.role_changed", u.id, p.user_id)
        await db.commit()
        return person_view(u)


@router.patch("/account")
async def account(payload: AccountRequest, request: Request) -> dict[str, Any]:
    p = require_role(request, "OWNER")
    if not payload.name.strip():
        raise HTTPException(422, "Account name required")
    async with request_session(request) as db:
        row = await db.get(Account, p.account_id)
        if row is None:
            raise HTTPException(404, "Account not found")
        row.name = payload.name.strip()
        row.updated_at = datetime.now(UTC)
        audit(db, p.account_id, "account.modified", row.id, p.user_id)
        await db.commit()
        return {"id": str(row.id), "name": row.name}


@router.get("/alert-channels")
async def channels(request: Request) -> list[dict[str, Any]]:
    require_role(request, "OWNER")
    async with request_session(request) as db:
        return [
            {
                "id": str(r.id),
                "name": r.name,
                "kind": r.kind,
                "enabled": r.enabled,
                "created_at": r.created_at,
            }
            for r in (await db.scalars(select(AlertChannel).limit(100))).all()
        ]


@router.post("/alert-channels", status_code=201)
async def add_channel(payload: ChannelRequest, request: Request) -> dict[str, Any]:
    p = require_role(request, "OWNER")
    if not request.app.state.settings.integration_encryption_key.get_secret_value():
        raise HTTPException(503, "Integration encryption is not configured")
    if payload.kind == "email":
        from app.saas.security import normalized_email

        try:
            destination = normalized_email(payload.destination)
        except ValueError:
            raise HTTPException(422, "Valid recipient email required") from None
        return await save_channel(payload, destination, request, p)
    url = urlparse(payload.destination)
    if (
        url.username
        or url.password
        or url.fragment
        or not url.hostname
        or (
            url.scheme != "https"
            and not (
                request.app.state.settings.environment != "production"
                and url.scheme == "http"
                and url.hostname in {"127.0.0.1", "localhost", "::1"}
            )
        )
    ):
        raise HTTPException(422, "Use HTTPS or a development loopback receiver")
    local_receiver = request.app.state.settings.environment != "production" and url.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if (
        not local_receiver
        and url.hostname not in request.app.state.settings.saas_alert_allowed_hosts
    ):
        raise HTTPException(422, "The operator must allow this integration hostname")
    if payload.kind == "slack" and url.hostname != "hooks.slack.com" and not local_receiver:
        raise HTTPException(422, "Use an official Slack webhook hostname")
    return await save_channel(payload, payload.destination, request, p)


async def save_channel(
    payload: ChannelRequest, destination: str, request: Request, p: Any
) -> dict[str, Any]:
    async with request_session(request) as db:
        row = AlertChannel(
            account_id=p.account_id,
            name=payload.name,
            kind=payload.kind,
            destination=encrypt_destination(destination, p.account_id, request.app.state.settings),
        )
        db.add(row)
        await db.flush()
        audit(db, p.account_id, "alert.integration_modified", row.id, p.user_id)
        await db.commit()
        return {"id": str(row.id), "name": row.name, "kind": row.kind, "enabled": row.enabled}


@router.get("/audit-events")
async def audit_events(request: Request, before: datetime | None = None) -> list[dict[str, Any]]:
    require_role(request, "OWNER")
    async with request_session(request) as db:
        query = (
            select(AuditEvent)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(100)
        )
        if before:
            if before.tzinfo is None:
                raise HTTPException(422, "Timezone required")
            query = query.where(AuditEvent.created_at < before)
        return [
            {
                "id": str(r.id),
                "action": r.action,
                "resource_id": r.resource_id,
                "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
                "created_at": r.created_at,
            }
            for r in (await db.scalars(query)).all()
        ]


@router.post("/alert-channels/{channel_id}/disable", status_code=204)
async def disable_channel(channel_id: UUID, request: Request) -> None:
    p = require_role(request, "OWNER")
    async with request_session(request) as db:
        row = await get_for_account(db, AlertChannel, channel_id)
        if row is None:
            raise HTTPException(404, "Alert channel not found")
        row.enabled = False
        audit(db, p.account_id, "alert.integration_modified", row.id, p.user_id)
        await db.commit()


RESOURCE_MODELS = {
    "predictions": Prediction,
    "incidents": Incident,
    "alerts": Alert,
    "experiments": ChaosExperiment,
    "remediation-proposals": RemediationProposal,
    "alert-channels": AlertChannel,
    "agent-credentials": AgentCredential,
}


@router.get("/resources/{resource}/{identifier}")
async def resource_detail(resource: str, identifier: UUID, request: Request) -> dict[str, Any]:
    model = RESOURCE_MODELS.get(resource)
    if model is None:
        raise HTTPException(404, "Resource not found")
    if resource in {"agent-credentials", "alert-channels"}:
        require_role(request, "OWNER", "ADMIN" if resource == "agent-credentials" else "OWNER")
    async with request_session(request) as db:
        row = await get_for_account(db, model, identifier)
        if row is None:
            raise HTTPException(404, "Resource not found")
        excluded = {"key_hash", "destination", "payload", "parameters"}
        return {
            c.name: getattr(row, c.name) for c in model.__table__.columns if c.name not in excluded
        }


@router.post("/remediation/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: UUID, request: Request) -> dict[str, Any]:
    """Record human review only; this endpoint never runs an action."""
    p = require_role(request, "OWNER", "ADMIN")
    async with request_session(request) as db:
        row = await db.scalar(
            select(RemediationProposal)
            .where(RemediationProposal.id == proposal_id)
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "Proposal not found")
        if row.approval_status != "approved":
            row.approval_status = "approved"
            row.approved_at = datetime.now(UTC)
            audit(db, p.account_id, "remediation.approved", row.id, p.user_id)
        await db.commit()
        return {
            "id": str(row.id),
            "approval_status": row.approval_status,
            "execution_status": row.execution_status,
            "execution_requested": False,
        }
