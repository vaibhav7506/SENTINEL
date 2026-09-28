from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, desc, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DEMO_ACCOUNT_ID = UUID("00000000-0000-4000-8000-000000000001")


class TenantOwned:
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id"),
        default=lambda: DEMO_ACCOUNT_ID,
        server_default=text("'00000000-0000-4000-8000-000000000001'::uuid"),
        index=True,
    )


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Identity, Base):
    __tablename__ = "accounts"
    name: Mapped[str] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(TenantOwned, Identity, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('OWNER','ADMIN','MEMBER')", name="user_role"),)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(12))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Identity, Base):
    __tablename__ = "auth_sessions"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnrollmentToken(TenantOwned, Identity, Base):
    __tablename__ = "enrollment_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(24))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentCredential(TenantOwned, Identity, Base):
    __tablename__ = "agent_credentials"
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id"))
    key_prefix: Mapped[str] = mapped_column(String(32), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertChannel(TenantOwned, Identity, Base):
    __tablename__ = "alert_channels"
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    # Versioned authenticated ciphertext. Never returned by browser APIs.
    destination: Mapped[str] = mapped_column(String(4096))
    enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class AuditEvent(TenantOwned, Identity, Base):
    __tablename__ = "audit_events"
    actor_user_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class MetricSample(TenantOwned, Base):
    __tablename__ = "metrics"
    __table_args__ = (
        Index("ix_metrics_host_time", "host_id", "observed_at"),
        Index("metrics_observed_at_idx", desc("observed_at")),
        Index("ix_metrics_account_host_time", "account_id", "host_id", desc("observed_at")),
    )
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    values: Mapped[dict[str, float]] = mapped_column(JSONB)


TENANT_TABLES = (
    "hosts",
    "feature_windows",
    "predictions",
    "incidents",
    "alerts",
    "chaos_experiments",
    "failure_events",
    "service_observations",
    "remediation_proposals",
    "users",
    "enrollment_tokens",
    "agent_credentials",
    "alert_channels",
    "audit_events",
    "metrics",
    "auth_sessions",
)
