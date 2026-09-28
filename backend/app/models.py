"""Persistence contracts for telemetry features, experiments, and future inference."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.saas.models import TenantOwned


class Host(TenantOwned, Base):
    __tablename__ = "hosts"
    __table_args__ = (
        CheckConstraint(
            "reporting_interval_seconds BETWEEN 5 AND 300",
            name="hosts_reporting_interval_seconds_check",
        ),
    )
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    environment: Mapped[str] = mapped_column(String(32))
    service_job: Mapped[str | None] = mapped_column(String(256))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str] = mapped_column(String(256), default="", server_default="")
    hostname: Mapped[str] = mapped_column(String(256), default="", server_default="")
    agent_version: Mapped[str] = mapped_column(String(64), default="", server_default="")
    platform: Mapped[str] = mapped_column(String(64), default="", server_default="")
    architecture: Mapped[str] = mapped_column(String(64), default="", server_default="")
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")
    reporting_interval_seconds: Mapped[int] = mapped_column(default=30, server_default="30")
    capacity: Mapped[dict[str, float]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeatureWindow(TenantOwned, Base):
    __tablename__ = "feature_windows"
    __table_args__ = (
        CheckConstraint("window_start < window_end", name="feature_window_bounds"),
        Index("ix_feature_windows_host_time", "host_id", "window_end"),
        Index("feature_windows_window_end_idx", desc("window_end")),
    )
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id"), primary_key=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(80), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    schema_hash: Mapped[str] = mapped_column(String(64))
    feature_names: Mapped[list[str]] = mapped_column(JSONB)
    feature_values: Mapped[list[float | None]] = mapped_column(JSONB)
    features: Mapped[dict[str, float | None]] = mapped_column(JSONB)
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Record:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelVersion(Record, Base):
    __tablename__ = "model_versions"
    __table_args__ = (CheckConstraint("threshold BETWEEN 0 AND 1", name="model_threshold"),)
    version: Mapped[str] = mapped_column(String(128), unique=True)
    schema_version: Mapped[str] = mapped_column(String(80))
    schema_hash: Mapped[str] = mapped_column(String(64))
    artifact_uri: Mapped[str] = mapped_column(String(1024))
    threshold: Mapped[float]
    training_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ChaosExperiment(TenantOwned, Record, Base):
    __tablename__ = "chaos_experiments"
    __table_args__ = (
        CheckConstraint("environment IN ('development','test','demo')", name="chaos_environment"),
    )
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id"))
    environment: Mapped[str] = mapped_column(String(32))
    failure_type: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expected_degradation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observed_degradation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class FailureEvent(TenantOwned, Record, Base):
    __tablename__ = "failure_events"
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id"))
    experiment_id: Mapped[UUID | None] = mapped_column(ForeignKey("chaos_experiments.id"))
    failure_type: Mapped[str] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criterion: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Prediction(TenantOwned, Record, Base):
    __tablename__ = "predictions"
    __table_args__ = (
        CheckConstraint("probability BETWEEN 0 AND 1", name="prediction_probability"),
        CheckConstraint("horizon_seconds > 0", name="prediction_horizon"),
        UniqueConstraint(
            "host_id", "model_version_id", "feature_window_end", name="prediction_once"
        ),
    )
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id"))
    model_version_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"))
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    feature_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[str] = mapped_column(String(80))
    probability: Mapped[float]
    anomaly_score: Mapped[float | None]
    horizon_seconds: Mapped[int]
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Incident(TenantOwned, Record, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index(
            "one_open_incident_per_host",
            "host_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id"))
    model_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("model_versions.id"))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    prediction_id: Mapped[UUID | None] = mapped_column(ForeignKey("predictions.id"))
    failure_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("failure_events.id"))
    status: Mapped[str] = mapped_column(String(32), default="open")
    predicted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alert(TenantOwned, Record, Base):
    __tablename__ = "alerts"
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"))
    channel: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(256))


class EvaluationRun(Record, Base):
    __tablename__ = "evaluation_runs"
    model_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("model_versions.id"))
    dataset_reference: Mapped[str] = mapped_column(String(1024))
    split_definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RemediationProposal(TenantOwned, Record, Base):
    __tablename__ = "remediation_proposals"
    __table_args__ = (
        UniqueConstraint("incident_id", "runbook_reference", name="proposal_once"),
        CheckConstraint(
            "submission_status IN ('pending','sending','submitted','unknown','cancelled')",
            name="proposal_submission_state",
        ),
        CheckConstraint(
            "execution_status = 'not_started' OR approval_status = 'approved'",
            name="proposal_execution_requires_approval",
        ),
    )
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"))
    prediction_id: Mapped[UUID | None] = mapped_column(ForeignKey("predictions.id"))
    runbook_reference: Mapped[str] = mapped_column(String(1024))
    suggested_action: Mapped[str] = mapped_column(
        String(80), default="collect_diagnostics", server_default="collect_diagnostics"
    )
    reason: Mapped[str] = mapped_column(String(4096), default="", server_default="")
    adapter_kind: Mapped[str] = mapped_column(String(32), default="mock", server_default="mock")
    runbookos_reference: Mapped[str | None] = mapped_column(String(1024))
    submission_status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(256))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    approval_status: Mapped[str] = mapped_column(String(32), default="pending")
    execution_status: Mapped[str] = mapped_column(String(32), default="not_started")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceObservation(TenantOwned, Record, Base):
    __tablename__ = "service_observations"
    __table_args__ = (Index("ix_service_observations_host_time", "host_id", "observed_at"),)
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id"))
    experiment_id: Mapped[UUID | None] = mapped_column(ForeignKey("chaos_experiments.id"))
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latency_seconds: Mapped[float]
    status_code: Mapped[int | None]
    outcome: Mapped[str] = mapped_column(String(32))
    breached: Mapped[bool]
    criteria: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
