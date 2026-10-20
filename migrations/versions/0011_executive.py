"""Immutable scoped tool approvals and non-retrying execution reservations."""

from alembic import op

revision = "0011_executive"
down_revision = "0009_exploration"


def upgrade():
    for sql in (
        """CREATE TABLE tool_tasks (
            task_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
            request_sha256 TEXT NOT NULL, scope_sha256 TEXT NOT NULL,
            executor_id TEXT NOT NULL, capability TEXT NOT NULL,
            scope_json TEXT NOT NULL, risk_level INTEGER NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('WAITING_APPROVAL','QUEUED','LEASED',
                'RUNNING','SUCCEEDED','FAILED','UNKNOWN','CANCELLED')),
            captured_privacy_epoch INTEGER NOT NULL, lease_token TEXT, lease_until TEXT,
            reserved_micro_usd INTEGER NOT NULL DEFAULT 0,
            billed_micro_usd INTEGER NOT NULL DEFAULT 0, dispatched_at TEXT,
            result_event_id TEXT REFERENCES events(event_id), last_error_code TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, redacted INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE tool_approvals (
            approval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tool_tasks,
            scope_sha256 TEXT NOT NULL, decision TEXT NOT NULL,
            expires_at TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT
        )""",
        """CREATE TABLE tool_evidence (
            task_id TEXT NOT NULL REFERENCES tool_tasks,
            event_id TEXT NOT NULL REFERENCES events, PRIMARY KEY(task_id,event_id)
        )""",
        "CREATE INDEX idx_tool_evidence_event ON tool_evidence(event_id)",
        "CREATE INDEX idx_tool_tasks_state ON tool_tasks(state,created_at)",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM tool_tasks LIMIT 1").first():
        raise RuntimeError("EXECUTIVE_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    for table in ("tool_evidence", "tool_approvals", "tool_tasks"):
        op.drop_table(table)
