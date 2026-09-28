from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    environment: Literal["development", "test", "demo", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_read_token: SecretStr = SecretStr("")
    saas_enabled: bool = True
    saas_public_api_url: HttpUrl = HttpUrl("http://127.0.0.1:5173/api")
    saas_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    session_lifetime_seconds: int = Field(default=28800, ge=300, le=86400)
    saas_alert_allowed_hosts: list[str] = Field(default_factory=lambda: ["hooks.slack.com"])
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:16379/0")
    integration_encryption_key: SecretStr = SecretStr("")
    agent_reporting_interval_seconds: int = Field(default=30, ge=5, le=300)
    agent_stale_intervals: int = Field(default=3, ge=2, le=10)
    agent_offline_intervals: int = Field(default=10, ge=4, le=60)
    ingestion_host_per_minute: int = Field(default=120, ge=2, le=6000)
    ingestion_host_burst: int = Field(default=30, ge=1, le=1000)
    ingestion_account_per_minute: int = Field(default=6000, ge=2, le=100000)
    ingestion_account_burst: int = Field(default=500, ge=1, le=10000)
    metric_retention_days: int = Field(default=30, ge=1, le=365)
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""

    @model_validator(mode="after")
    def heartbeat_thresholds(self) -> Settings:
        if self.agent_offline_intervals <= self.agent_stale_intervals:
            raise ValueError("Offline intervals must exceed stale intervals")
        return self

    postgres_host: str = "localhost"
    postgres_port: int = Field(default=15432, ge=1, le=65535)
    postgres_db: str = "sentinel"
    postgres_user: str = "sentinel"
    postgres_password: SecretStr = SecretStr("")
    postgres_sslmode: Literal["disable", "require", "verify-full"] = "disable"
    configured_database_url: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("DATABASE_URL", "configured_database_url"),
    )
    prometheus_url: HttpUrl = HttpUrl("http://localhost:9090")
    prometheus_username: str = ""
    prometheus_password: SecretStr = SecretStr("")
    prometheus_readiness_mode: Literal["local", "query", "disabled"] = "local"
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    worker_heartbeat_seconds: float = Field(default=30.0, gt=0, le=300)

    feature_interval_seconds: int = Field(default=60, ge=15, le=3600)
    feature_window_seconds: int = Field(default=900, ge=60, le=86400)
    feature_freshness_seconds: int = Field(default=90, ge=15, le=600)
    feature_query_timeout_seconds: float = Field(default=15, gt=0, le=60)
    feature_host_service_map: dict[str, str] = Field(default_factory=dict)

    chaos_enabled: bool = False
    chaos_allowed_environments: list[str] = Field(default_factory=list)
    chaos_allowed_namespaces: list[str] = Field(default_factory=list)
    chaos_allowed_targets: list[str] = Field(default_factory=list)
    chaos_control_token: SecretStr = SecretStr("")
    chaos_demo_url: Literal["http://demo-service:8000", "http://127.0.0.1:8001"] = (
        "http://demo-service:8000"
    )
    observation_interval_seconds: int = Field(default=2, ge=1, le=3)
    observation_host_id: str = Field(default="docker-vm", min_length=1, max_length=128)
    observation_latency_slo_seconds: float = Field(default=0.5, ge=0.05, le=2)
    prediction_horizon_seconds: int = Field(default=600, ge=30, le=3600)
    dataset_recovery_exclusion_seconds: int = Field(default=900, ge=60, le=86400)

    inference_model_path: str = "artifacts/models/20260926T204848-mlp-1bcd8acd"
    inference_poll_seconds: int = Field(default=5, ge=1, le=60)
    inference_max_age_seconds: int = Field(default=90, ge=15, le=300)
    inference_metrics_port: int = Field(default=8004, ge=1024, le=65535)
    inference_metrics_bind_address: Literal["127.0.0.1", "0.0.0.0"] = "127.0.0.1"
    alert_cooldown_seconds: int = Field(default=300, ge=60, le=86400)
    alert_generic_webhook_url: SecretStr = SecretStr("")
    alert_slack_webhook_url: SecretStr = SecretStr("")
    dashboard_url: HttpUrl = HttpUrl("http://127.0.0.1:5173")
    llm_provider: Literal["disabled", "http"] = "disabled"
    llm_base_url: HttpUrl | None = None
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""

    runbookos_enabled: bool = False
    runbookos_base_url: HttpUrl | None = None
    runbookos_api_key: SecretStr = SecretStr("")
    runbookos_adapter: Literal["mock"] = "mock"
    runbookos_proposal_threshold: float = Field(default=0.995, ge=0, le=1)

    @field_validator("llm_base_url", "runbookos_base_url", mode="before")
    @classmethod
    def empty_llm_url(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def production_security(self) -> Settings:
        if (
            self.prometheus_url.username
            or self.prometheus_url.password
            or self.prometheus_url.query
            or self.prometheus_url.fragment
        ):
            raise ValueError("Prometheus URL must not contain credentials, query or fragment")
        has_password = bool(self.prometheus_password.get_secret_value())
        if bool(self.prometheus_username) != has_password:
            raise ValueError("Prometheus authentication requires both username and password")
        if has_password and self.prometheus_url.scheme != "https":
            raise ValueError("Authenticated Prometheus queries require HTTPS")
        if (
            self.saas_public_api_url.username
            or self.saas_public_api_url.password
            or self.saas_public_api_url.query
            or self.saas_public_api_url.fragment
        ):
            raise ValueError("Public API URL must not contain credentials, query or fragment")
        if self.environment == "production":
            if self.database_url.query.get("sslmode") not in {"require", "verify-full"}:
                raise ValueError("Production database requires SSL")
            if self.saas_enabled and len(self.integration_encryption_key.get_secret_value()) != 44:
                raise ValueError("Production requires a generated integration encryption key")
            if self.saas_enabled:
                if (
                    self.saas_public_api_url.scheme != "https"
                    or not self.saas_allowed_origins
                    or any(
                        not origin.startswith("https://") or "*" in origin
                        for origin in self.saas_allowed_origins
                    )
                ):
                    raise ValueError(
                        "Production SaaS requires HTTPS API and explicit HTTPS browser origins"
                    )
            elif len(self.api_read_token.get_secret_value()) < 32:
                raise ValueError("Production requires an API read token of at least 32 characters")
            if self.chaos_enabled:
                raise ValueError("Production chaos must be disabled")
        return self

    @property
    def database_url(self) -> URL:
        raw = self.configured_database_url.get_secret_value()
        if raw:
            try:
                url = make_url(raw)
                if (
                    url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}
                    or not url.host
                    or not url.database
                    or not url.username
                    or not url.password
                ):
                    raise ValueError
                mode = url.query.get("sslmode", self.postgres_sslmode)
                if not isinstance(mode, str) or mode not in {
                    "disable",
                    "require",
                    "verify-full",
                }:
                    raise ValueError
                return url.set(drivername="postgresql+psycopg").update_query_dict({"sslmode": mode})
            except Exception:
                raise ValueError("Invalid PostgreSQL DATABASE_URL") from None
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
            query={"sslmode": self.postgres_sslmode},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
