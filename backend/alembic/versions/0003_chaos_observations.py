"""Experiment lifecycle, observed recovery, and objective service probes."""

import sqlalchemy as sa
from alembic import op

revision = "0003_chaos_observations"
down_revision = "0002_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chaos_experiments",
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
    )
    op.add_column("failure_events", sa.Column("recovered_at", sa.DateTime(timezone=True)))
    op.create_table(
        "service_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("host_id", sa.String(128), sa.ForeignKey("hosts.id"), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("chaos_experiments.id")),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_seconds", sa.Float(), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("breached", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_service_observations_host_time", "service_observations", ["host_id", "observed_at"]
    )


def downgrade() -> None:
    op.drop_table("service_observations")
    op.drop_column("failure_events", "recovered_at")
    op.drop_column("chaos_experiments", "status")
