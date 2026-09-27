from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Provider(str, Enum):
    CLOUDFLARE = "cloudflare"
    VERCEL = "vercel"
    NEON = "neon"


class HealthStatus(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    CONNECTION_REFUSED = "connection_refused"
    DNS_FAILURE = "dns_failure"
    NON_2XX = "non_2xx"
    UNKNOWN = "unknown"


class ServiceConfig(BaseModel):
    name: str = Field(..., min_length=1)
    url: str = Field(..., pattern=r"^https?://")
    expected_status: int = Field(default=200, ge=100, le=599)
    expected_latency_ms: float = Field(default=1000.0, gt=0)
    provider: Provider
    provider_resource_id: str = Field(..., min_length=1)
    env_vars_expected: dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    base_delay_seconds: float = Field(default=2.0, gt=0)
    max_delay_seconds: float = Field(default=60.0, gt=0)
    exponential_base: float = Field(default=2.0, gt=1)


class CircuitBreakerPolicy(BaseModel):
    failure_threshold: int = Field(default=5, ge=1)
    recovery_timeout_seconds: float = Field(default=300.0, gt=0)
    half_open_max_calls: int = Field(default=3, ge=1)


class StormGuardPolicy(BaseModel):
    max_concurrent_remediations: int = Field(default=2, ge=1)
    cooldown_seconds: float = Field(default=60.0, gt=0)


class AlertPolicy(BaseModel):
    webhook_url: str | None = None
    webhook_type: str | None = None
    notify_on_incident: bool = True
    notify_on_prediction: bool = True
    prediction_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class PollingPolicy(BaseModel):
    interval_seconds: int = Field(default=300, ge=10)
    timeout_seconds: float = Field(default=5.0, gt=0)
    concurrent_limit: int = Field(default=10, ge=1)


class PolicyConfig(BaseModel):
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    circuit_breaker: CircuitBreakerPolicy = Field(default_factory=CircuitBreakerPolicy)
    storm_guard: StormGuardPolicy = Field(default_factory=StormGuardPolicy)
    alerting: AlertPolicy = Field(default_factory=AlertPolicy)
    polling: PollingPolicy = Field(default_factory=PollingPolicy)


class Settings(BaseSettings):
    cloudflare_api_token: str | None = None
    vercel_api_token: str | None = None
    neon_api_token: str | None = None
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    database_path: str = "data/metrics_history.db"
    model_path: str = "data/model.pt"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def load_services_config(path: str | Path) -> list[ServiceConfig]:
    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "services" not in data:
        raise ValueError("services.yaml must contain a 'services' key with a list of services")

    services = []
    for idx, svc_data in enumerate(data["services"]):
        try:
            services.append(ServiceConfig(**svc_data))
        except Exception as e:
            raise ValueError(f"Invalid service config at index {idx}: {e}") from e

    if not services:
        raise ValueError("At least one service must be configured")

    names = [s.name for s in services]
    if len(names) != len(set(names)):
        raise ValueError("Service names must be unique")

    return services


def load_policy_config(path: str | Path) -> PolicyConfig:
    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("policy.yaml must be a mapping")

    return PolicyConfig(**data.get("policy", {}))