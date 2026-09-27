"""Record each probe's actual criterion; old observations remain unknown."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_probe_criteria"
down_revision = "0003_chaos_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("service_observations", sa.Column("criteria", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("service_observations", "criteria")
