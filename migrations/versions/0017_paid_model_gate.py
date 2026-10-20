"""Atomic paid-model admission metadata; existing usage accounting remains authoritative."""

from alembic import op

revision = "0017_paid_model_gate"
down_revision = "0016_source_grants"


def upgrade():
    op.execute(
        "CREATE TABLE model_budget_windows (window_id TEXT PRIMARY KEY,"
        "contract_sha256 TEXT NOT NULL)"
    )
    op.execute(
        "CREATE TABLE model_admissions ("
        "request_id TEXT PRIMARY KEY REFERENCES model_usage(request_id),"
        "logical_request_id TEXT NOT NULL,attempt_id TEXT NOT NULL UNIQUE,"
        "attempt_number INTEGER NOT NULL,window_id TEXT NOT NULL REFERENCES model_budget_windows,"
        "contract_sha256 TEXT NOT NULL,payload_sha256 TEXT NOT NULL,snapshot_json TEXT NOT NULL,"
        "reserved_micro_usd INTEGER NOT NULL CHECK(reserved_micro_usd>0),"
        "input_bound INTEGER NOT NULL,output_bound INTEGER NOT NULL,lease_token TEXT NOT NULL,"
        "lease_until TEXT NOT NULL,io_started_at TEXT,"
        "journal_path TEXT NOT NULL,journal_id TEXT NOT NULL)"
    )
    op.execute("CREATE INDEX model_admission_logical ON model_admissions(logical_request_id)")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM model_admissions LIMIT 1").first():
        raise RuntimeError("PAID_BUDGET_DOWNGRADE_REQUIRES_RECONCILIATION")
    op.execute("DROP TABLE model_admissions")
    op.execute("DROP TABLE model_budget_windows")
