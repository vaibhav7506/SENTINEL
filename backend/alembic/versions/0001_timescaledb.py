"""Enable the required time-series extension without adding later-phase tables."""

from alembic import op

revision = "0001_timescaledb"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")


def downgrade() -> None:
    # Leave the extension intact: future hypertables or shared DB users may depend on it.
    pass
