"""Online scores, persistent delivery state and retry uniqueness."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005_online_inference"
down_revision = "0004_probe_criteria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("predictions", sa.Column("anomaly_score", sa.Float(), nullable=True))
    op.create_unique_constraint(
        "prediction_once", "predictions", ["host_id", "model_version_id", "feature_window_end"]
    )
    op.add_column("incidents", sa.Column("model_version_id", UUID(), nullable=True))
    op.create_foreign_key(
        "incident_model", "incidents", "model_versions", ["model_version_id"], ["id"]
    )
    op.add_column("incidents", sa.Column("summary", JSONB(), server_default="{}", nullable=False))
    op.create_index(
        "one_open_incident_per_host",
        "incidents",
        ["host_id"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.add_column(
        "alerts", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("alerts", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("alerts", sa.Column("last_error", sa.String(256), nullable=True))


def downgrade() -> None:
    for name in ("last_error", "next_attempt_at", "attempt_count"):
        op.drop_column("alerts", name)
    op.drop_index("one_open_incident_per_host", table_name="incidents")
    op.drop_column("incidents", "summary")
    op.drop_constraint("incident_model", "incidents", type_="foreignkey")
    op.drop_column("incidents", "model_version_id")
    op.drop_constraint("prediction_once", "predictions", type_="unique")
    op.drop_column("predictions", "anomaly_score")
