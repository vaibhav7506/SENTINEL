"""Fixed-target demo execution policy, enforced independently of the injector."""

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.models import Host


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["demo-service"] = "demo-service"
    host_id: Literal["docker-vm", "sentinel-demo"] = "docker-vm"
    namespace: Literal["sentinel-demo"] = "sentinel-demo"
    failure_type: Literal["application_latency", "http_error"]
    duration_seconds: int = Field(default=45, ge=10, le=120)
    latency_ms: int = Field(default=750, ge=1, le=2000)


def enforce_scope(settings: Settings, request: ExperimentRequest) -> None:
    if not settings.chaos_enabled:
        raise HTTPException(403, "CHAOS_ENABLED must be true")
    if (
        settings.environment not in {"development", "test", "demo"}
        or settings.environment not in settings.chaos_allowed_environments
        or request.namespace not in settings.chaos_allowed_namespaces
        or request.target not in settings.chaos_allowed_targets
    ):
        raise HTTPException(403, "Experiment is outside the chaos allowlist")
    if len(settings.chaos_control_token.get_secret_value()) < 32:
        raise HTTPException(503, "Chaos control authorization is not configured")


def enforce_policy(settings: Settings, request: ExperimentRequest, host: Host | None) -> None:
    enforce_scope(settings, request)
    if (
        host is None
        or host.environment != settings.environment
        or host.service_job != request.target
    ):
        raise HTTPException(403, "Host identity or mapped service does not match the allowed demo")
