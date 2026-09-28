"""Bounded push telemetry and encrypted integrations; no historical rows rewritten."""

from alembic import op

revision = "0008_push_pipeline"
down_revision = "0007_saas_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hosts ADD COLUMN reporting_interval_seconds integer NOT NULL DEFAULT 30 "
        "CHECK (reporting_interval_seconds BETWEEN 5 AND 300)"
    )
    op.execute("ALTER TABLE hosts ADD COLUMN capacity jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("CREATE INDEX ix_agent_credentials_key_prefix ON agent_credentials(key_prefix)")
    op.execute(
        "CREATE INDEX ix_metrics_account_host_time ON metrics(account_id,host_id,observed_at DESC)"
    )
    op.execute("ALTER TABLE alert_channels ALTER COLUMN destination TYPE varchar(4096)")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup for rollback; never discard tenant telemetry")
