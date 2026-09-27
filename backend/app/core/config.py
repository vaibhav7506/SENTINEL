from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    environment: Literal["development", "test", "demo", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_read_token: SecretStr = SecretStr("")
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=15432, ge=1, le=65535)
    postgres_db: str = "sentinel"
    postgres_user: str = "sentinel"
    postgres_password: SecretStr = SecretStr("")
    postgres_sslmode: Literal["disable", "require", "verify-full"] = "disable"
    prometheus_url: HttpUrl = HttpUrl("http://localhost:9090")
    prometheus_username: str = ""
    prometheus_password: SecretStr = SecretStr("")
    prometheus_readiness_mode: Literal["local", "query"] = "local"
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
        if self.environment == "production":
            if len(self.api_read_token.get_secret_value()) < 32:
                raise ValueError("Production requires an API read token of at least 32 characters")
            if self.chaos_enabled:
                raise ValueError("Production chaos must be disabled")
        return self

    @property
    def database_url(self) -> URL:
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
