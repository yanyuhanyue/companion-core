"""Optional discover query chosen by Core; absent for every earlier request."""

from alembic import op

revision = "0051_exploration_query"
down_revision = "0050_watch_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE exploration_requests ADD COLUMN query TEXT "
               "CHECK(query IS NULL OR (length(query) BETWEEN 2 AND 100 AND operation='discover'))")


def downgrade():
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM exploration_requests WHERE query IS NOT NULL LIMIT 1"
    ).first():
        raise RuntimeError("Queried discover requests block downgrade")
    op.execute("ALTER TABLE exploration_requests DROP COLUMN query")
