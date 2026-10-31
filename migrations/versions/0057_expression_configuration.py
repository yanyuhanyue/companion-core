"""Expression values extend task snapshots without replacing existing identities."""

from alembic import op

revision = "0057_expression_configuration"
down_revision = "0056_watch_ledger_goal_reads"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE operator_runtime_snapshots ADD COLUMN expression_json TEXT")


def downgrade():
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM operator_runtime_snapshots WHERE expression_json IS NOT NULL LIMIT 1",
    ).first():
        raise RuntimeError("CONFIGURATION_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("ALTER TABLE operator_runtime_snapshots DROP COLUMN expression_json")
