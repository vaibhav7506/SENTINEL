"""Authenticated, bounded, server-owned batched telemetry."""

import hmac
import math
import re
import time
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.features.schema import APP_METRICS, HOST_METRICS
from app.models import Host
from app.saas.models import AgentCredential, MetricSample
from app.saas.repository import account_session
from app.saas.security import digest

router = APIRouter(tags=["agent push ingestion"])
CAPACITY = {"memory_capacity_bytes", "disk_capacity_bytes", "cpu_count"}
ALLOWED = (
    set(HOST_METRICS + APP_METRICS) | CAPACITY | {"sample_timestamp_seconds", "sample_success"}
)


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    timestamp: datetime
    values: dict[str, float] = Field(min_length=1, max_length=32)

    @field_validator("values")
    @classmethod
    def bounds(cls, values: dict[str, float]) -> dict[str, float]:
        if not set(values) <= ALLOWED:
            raise ValueError("Unsupported metric")
        for name, value in values.items():
            upper = 1e16
            if name.endswith("_percent"):
                upper = 100
            elif name in {"error_rate", "sample_success"}:
                upper = 1
            elif name == "cpu_count":
                upper = 4096
            elif name in {"process_count", "cpu_load_1m", "cpu_load_5m", "cpu_load_15m"}:
                upper = 1e6
            elif name == "latency_p95_seconds":
                upper = 3600
            if not math.isfinite(value) or not 0 <= value <= upper:
                raise ValueError("Metric outside supported range")
        return values


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    samples: list[Sample] = Field(min_length=1, max_length=120)
    reporting_interval_seconds: int = Field(default=30, ge=5, le=300)
    agent_version: str = Field(default="", max_length=64)


def heartbeat(host: Host, settings: Settings, now: datetime | None = None) -> str:
    if host.status != "active":
        return "Offline"
    age = ((now or datetime.now(UTC)) - host.last_seen).total_seconds()
    if age <= host.reporting_interval_seconds * settings.agent_stale_intervals:
        return "Online"
    if age <= host.reporting_interval_seconds * settings.agent_offline_intervals:
        return "Stale"
    return "Offline"


async def authenticate_agent(request: Request) -> tuple[UUID, str, UUID]:
    authorization = request.headers.get("authorization", "")
    raw = authorization.removeprefix("Bearer ")
    if not authorization.startswith("Bearer ") or not re.fullmatch(
        r"sk_host_[0-9a-f]{8}_[A-Za-z0-9_-]{32,100}", raw
    ):
        request.app.state.ingestion_rejections.labels("credential").inc()
        raise HTTPException(401, "Agent credential required")
    async with async_sessionmaker(request.app.state.engine)() as db:
        candidates = (
            await db.scalars(
                select(AgentCredential)
                .where(AgentCredential.key_prefix.in_([raw[:17], raw[:20]]))
                .limit(16)
            )
        ).all()
        candidate = next(
            (key for key in candidates if hmac.compare_digest(key.key_hash, digest(raw))), None
        )
        if candidate is None or candidate.revoked_at is not None:
            request.app.state.ingestion_rejections.labels("credential").inc()
            raise HTTPException(401, "Agent credential invalid or revoked")
        return candidate.account_id, candidate.host_id, candidate.id


# One atomic decision for both buckets, using Redis server time across API replicas.
LIMIT_SCRIPT = """
local clock=redis.call('TIME'); local now=clock[1]+clock[2]/1000000
local remaining={}; local wait=0
for i=1,2 do
 local rate=tonumber(ARGV[(i-1)*2+1])/60; local cap=tonumber(ARGV[(i-1)*2+2])
 local old=redis.call('HMGET',KEYS[i],'tokens','time')
 local tokens=cap
 if old[1] then tokens=math.min(cap,tonumber(old[1])+math.max(0,now-tonumber(old[2]))*rate) end
 remaining[i]=tokens
 if tokens<1 then wait=math.max(wait,(1-tokens)/rate) end
end
if wait>0 then return math.ceil(wait) end
for i=1,2 do
 redis.call('HSET',KEYS[i],'tokens',remaining[i]-1,'time',now)
 redis.call('EXPIRE',KEYS[i],3600)
end
return 0
"""


async def enforce_limit(request: Request, account: UUID, credential: UUID) -> None:
    config = request.app.state.settings
    try:
        delay = await request.app.state.redis.eval(
            LIMIT_SCRIPT,
            2,
            f"sentinel:{{{account}}}:host:{credential}",
            f"sentinel:{{{account}}}:account",
            config.ingestion_host_per_minute,
            config.ingestion_host_burst,
            config.ingestion_account_per_minute,
            config.ingestion_account_burst,
        )
    except Exception:
        request.app.state.ingestion_rejections.labels("limiter_unavailable").inc()
        raise HTTPException(503, "Ingestion temporarily unavailable") from None
    if delay:
        request.app.state.ingestion_rejections.labels("rate_limit").inc()
        raise HTTPException(429, "Agent rate limit", headers={"Retry-After": str(delay)})


@router.post("/agent/v1/metrics")
async def ingest(request: Request) -> dict[str, Any]:
    started = time.perf_counter()
    account, host_id, credential_id = request.state.agent_identity
    await enforce_limit(request, account, credential_id)
    try:
        payload = Batch.model_validate_json(await request.body())
        now = datetime.now(UTC)
        for sample in payload.samples:
            if (
                sample.timestamp.tzinfo is None
                or not -30 <= (now - sample.timestamp).total_seconds() <= 1800
            ):
                raise ValueError("Timestamp outside accepted bounds")
        if len({s.timestamp for s in payload.samples}) != len(payload.samples):
            raise ValueError("Repeated sample timestamp")
    except ValidationError, ValueError:
        request.app.state.ingestion_rejections.labels("payload").inc()
        raise HTTPException(422, "Invalid telemetry batch") from None
    async with account_session(request.app.state.engine, account) as db:
        key = await db.scalar(
            select(AgentCredential).where(AgentCredential.id == credential_id).with_for_update()
        )
        host = await db.scalar(select(Host).where(Host.id == host_id))
        if key is None or key.revoked_at or host is None or host.status != "active":
            raise HTTPException(401, "Agent credential unavailable")
        result = await db.execute(
            insert(MetricSample)
            .values(
                [
                    {
                        "account_id": account,
                        "host_id": host_id,
                        "observed_at": s.timestamp,
                        "values": s.values,
                    }
                    for s in payload.samples
                ]
            )
            .on_conflict_do_nothing()
            .returning(MetricSample.observed_at)
        )
        count = len(result.all())
        key.last_used_at = host.last_seen = now
        host.reporting_interval_seconds = payload.reporting_interval_seconds
        if payload.agent_version:
            host.agent_version = payload.agent_version
        host.capacity = {
            **host.capacity,
            **{
                k: v
                for k, v in max(payload.samples, key=lambda s: s.timestamp).values.items()
                if k in CAPACITY
            },
        }
        await db.commit()
    request.app.state.ingestion_accepted.inc(count)
    request.app.state.ingestion_duration.observe(time.perf_counter() - started)
    return {
        "accepted_samples": count,
        "duplicate_samples": len(payload.samples) - count,
        "schema_version": 1,
    }
