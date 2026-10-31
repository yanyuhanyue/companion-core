"""Freeze an opt-in account recommendation source on each discovery request."""

from alembic import op

revision = "0058_bili_discovery"
down_revision = "0057_expression_configuration"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE exploration_requests ADD COLUMN discovery_mode TEXT NOT NULL "
               "DEFAULT 'default' CHECK(discovery_mode='default' OR "
               "(discovery_mode='recommend' AND operation='discover' AND query IS NULL))")


def downgrade():
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM exploration_requests WHERE discovery_mode!='default' LIMIT 1"
    ).first():
        raise RuntimeError("Recommendation requests block downgrade")
    op.execute("ALTER TABLE exploration_requests DROP COLUMN discovery_mode")
