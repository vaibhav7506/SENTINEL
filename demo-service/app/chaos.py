"""Bounded in-process faults for this demo application only."""

import hmac
import json
import os
import time
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/control/chaos", tags=["local demo chaos"])


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: UUID
    failure_type: Literal["application_latency", "http_error"]
    duration_seconds: int = Field(ge=10, le=120)
    latency_ms: int = Field(default=750, ge=1, le=2000)
    namespace: Literal["sentinel-demo"]


def authorize(authorization: str | None) -> None:
    environment = os.getenv("ENVIRONMENT", "development")
    try:
        allowed = json.loads(os.getenv("CHAOS_ALLOWED_ENVIRONMENTS", "[]"))
        namespaces = json.loads(os.getenv("CHAOS_ALLOWED_NAMESPACES", "[]"))
        targets = json.loads(os.getenv("CHAOS_ALLOWED_TARGETS", "[]"))
    except ValueError:
        raise HTTPException(403, "Invalid chaos allowlist") from None
    token = os.getenv("CHAOS_CONTROL_TOKEN", "")
    if (
        os.getenv("CHAOS_ENABLED", "false").lower() != "true"
        or environment not in {"development", "test", "demo"}
        or not all(isinstance(value, list) for value in (allowed, namespaces, targets))
        or environment not in allowed
        or "sentinel-demo" not in namespaces
        or "demo-service" not in targets
    ):
        raise HTTPException(403, "Demo chaos is disabled or outside the allowlist")
    if len(token) < 32 or not hmac.compare_digest(authorization or "", "Bearer " + token):
        raise HTTPException(401, "Invalid control authorization")


class FaultState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.request: FaultRequest | None = None
        self.deadline = 0.0
        self.started_at: datetime | None = None
        self.expires_at: datetime | None = None

    def view(self) -> dict[str, object]:
        with self.lock:
            active = self.request is not None and time.monotonic() < self.deadline
            return {
                "active": active,
                "experiment_id": str(self.request.experiment_id) if self.request else None,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            }

    def start(self, request: FaultRequest) -> dict[str, object]:
        with self.lock:
            if self.request is not None and time.monotonic() < self.deadline:
                raise HTTPException(409, "A bounded experiment is already active")
            self.request = request
            self.deadline = time.monotonic() + request.duration_seconds
            self.started_at = datetime.now(UTC)
            self.expires_at = self.started_at + timedelta(seconds=request.duration_seconds)
        return self.view()

    def effect(self) -> tuple[str | None, float]:
        with self.lock:
            if self.request is None or time.monotonic() >= self.deadline:
                return None, 0
            return self.request.failure_type, self.request.latency_ms / 1000

    def cancel(self, experiment_id: UUID) -> dict[str, object]:
        with self.lock:
            if self.request is None or self.request.experiment_id != experiment_id:
                raise HTTPException(404, "Experiment is not the current demo fault")
            self.deadline = 0
        return self.view()


state = FaultState()


@router.post("")
async def start(
    request: FaultRequest, authorization: str | None = Header(default=None)
) -> dict[str, object]:
    authorize(authorization)
    return state.start(request)


@router.get("")
async def status(authorization: str | None = Header(default=None)) -> dict[str, object]:
    authorize(authorization)
    return state.view()


@router.delete("/{experiment_id}")
async def cancel(
    experiment_id: UUID, authorization: str | None = Header(default=None)
) -> dict[str, object]:
    authorize(authorization)
    return state.cancel(experiment_id)
