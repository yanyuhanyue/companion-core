"""Versioned CNY limits and cost snapshots attached to existing USD accounting."""

from alembic import op

revision = "0026_cloud_budget"
down_revision = "0025_meme_captions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE cloud_budget_policies (version INTEGER PRIMARY KEY,"
        "config_json TEXT NOT NULL CHECK(json_valid(config_json)),"
        "config_sha256 TEXT NOT NULL,timezone TEXT NOT NULL,effective_at TEXT NOT NULL,"
        "fx_valid_from TEXT NOT NULL,"
        "fx_valid_until TEXT NOT NULL)"
    )
    op.execute(
        "CREATE TABLE cloud_budget_changes (idempotency_key TEXT PRIMARY KEY,"
        "request_sha256 TEXT NOT NULL,version INTEGER NOT NULL "
        "REFERENCES cloud_budget_policies(version),created_at TEXT NOT NULL)"
    )
    op.execute(
        "CREATE TABLE cloud_cost_snapshots ("
        "reference_type TEXT NOT NULL CHECK(reference_type IN ('model','tool')),"
        "reference_id TEXT NOT NULL,policy_version INTEGER NOT NULL "
        "REFERENCES cloud_budget_policies(version),"
        "task_kind TEXT NOT NULL,task_id TEXT NOT NULL,local_day TEXT NOT NULL,"
        "occurred_at TEXT NOT NULL,dispatched_at TEXT,"
        "snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),"
        "reserved_micro_usd INTEGER NOT NULL CHECK(reserved_micro_usd>=0),"
        "accounted_micro_usd INTEGER NOT NULL CHECK(accounted_micro_usd>=0),"
        "amount_micro_cny INTEGER NOT NULL CHECK(amount_micro_cny>=0),"
        "state TEXT NOT NULL CHECK(state IN ('RESERVED','SETTLED','UNKNOWN')),"
        "legacy INTEGER NOT NULL CHECK(legacy IN (0,1)),reconciliation_sha256 TEXT,"
        "updated_at TEXT NOT NULL,"
        "PRIMARY KEY(reference_type,reference_id))"
    )
    op.execute("CREATE INDEX cloud_cost_day ON cloud_cost_snapshots(local_day,state)")
    op.execute("CREATE INDEX cloud_cost_task ON cloud_cost_snapshots(task_kind,task_id)")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM cloud_budget_policies LIMIT 1").first():
        raise RuntimeError("CLOUD_BUDGET_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    for table in ("cloud_cost_snapshots", "cloud_budget_changes", "cloud_budget_policies"):
        op.execute(f"DROP TABLE {table}")
