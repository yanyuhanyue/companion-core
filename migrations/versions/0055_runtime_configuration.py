"""Immutable configuration identities over existing native task ownership."""

from alembic import op

revision = "0055_runtime_configuration"
down_revision = "0054_life_autonomy_projection"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE operator_runtime_snapshots (snapshot_id TEXT PRIMARY KEY,"
               "version INTEGER NOT NULL,base_sha256 TEXT NOT NULL,"
               "chat_time_labels INTEGER NOT NULL CHECK(chat_time_labels IN (0,1)),"
               "created_at TEXT NOT NULL)")
    op.execute("CREATE TABLE operator_task_configuration (task_key TEXT PRIMARY KEY,"
               "snapshot_id TEXT NOT NULL REFERENCES operator_runtime_snapshots(snapshot_id),"
               "binding_sha256 TEXT NOT NULL,created_at TEXT NOT NULL)")


def downgrade():
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM operator_task_configuration LIMIT 1",
    ).first():
        raise RuntimeError("CONFIGURATION_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("DROP TABLE operator_task_configuration")
    op.execute("DROP TABLE operator_runtime_snapshots")
