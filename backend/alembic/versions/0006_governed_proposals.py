"""Durable advisory proposals; external execution still requires human approval."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0006_governed_proposals"
down_revision = "0005_online_inference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("remediation_proposals", sa.Column("prediction_id", UUID(), nullable=True))
    op.create_foreign_key(
        "proposal_prediction", "remediation_proposals", "predictions", ["prediction_id"], ["id"]
    )
    for name, length, default in [
        ("suggested_action", 80, "collect_diagnostics"),
        ("reason", 4096, ""),
        ("adapter_kind", 32, "mock"),
        ("submission_status", 32, "pending"),
    ]:
        op.add_column(
            "remediation_proposals",
            sa.Column(name, sa.String(length), server_default=default, nullable=False),
        )
    op.add_column(
        "remediation_proposals", sa.Column("runbookos_reference", sa.String(1024), nullable=True)
    )
    op.add_column(
        "remediation_proposals",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("remediation_proposals", sa.Column("last_error", sa.String(256), nullable=True))
    op.create_unique_constraint(
        "proposal_once", "remediation_proposals", ["incident_id", "runbook_reference"]
    )
    op.create_check_constraint(
        "proposal_submission_state",
        "remediation_proposals",
        "submission_status IN ('pending','sending','submitted','unknown','cancelled')",
    )
    op.create_check_constraint(
        "proposal_execution_requires_approval",
        "remediation_proposals",
        "execution_status = 'not_started' OR approval_status = 'approved'",
    )


def downgrade() -> None:
    for name in ["proposal_execution_requires_approval", "proposal_submission_state"]:
        op.drop_constraint(name, "remediation_proposals", type_="check")
    op.drop_constraint("proposal_once", "remediation_proposals", type_="unique")
    op.drop_constraint("proposal_prediction", "remediation_proposals", type_="foreignkey")
    for name in [
        "last_error",
        "submitted_at",
        "runbookos_reference",
        "submission_status",
        "adapter_kind",
        "reason",
        "suggested_action",
        "prediction_id",
    ]:
        op.drop_column("remediation_proposals", name)
