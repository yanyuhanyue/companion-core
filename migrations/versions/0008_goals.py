"""Durable goals, accepted commitments and bounded autonomous wake decisions."""

from alembic import op

revision = "0008_goals"
down_revision = "0007_consolidation"


def upgrade():
    for sql in (
        """CREATE TABLE goals (
            goal_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('self','commitment')),
            topic TEXT NOT NULL, scope_json TEXT NOT NULL, proposal_key TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL CHECK(state IN ('CANDIDATE','ACCEPTED','QUEUED','RUNNING',
                'DONE','WAITING_INPUT','WAITING_RESOURCE','RETRY_WAIT','FAILED','CANCELLED')),
            priority INTEGER NOT NULL, next_due_at TEXT NOT NULL,
            last_error_code TEXT, version INTEGER NOT NULL DEFAULT 1,
            result_event_id TEXT REFERENCES events(event_id), created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, redacted INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE goal_evidence (
            goal_id TEXT NOT NULL REFERENCES goals(goal_id),
            event_id TEXT NOT NULL REFERENCES events(event_id), PRIMARY KEY(goal_id,event_id)
        )""",
        """CREATE TABLE goal_steps (
            step_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL UNIQUE REFERENCES goals(goal_id),
            state TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','DONE','WAITING_RESOURCE',
                'RETRY_WAIT','FAILED','CANCELLED','EXECUTION_UNCERTAIN')),
            attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
            lease_token TEXT, lease_until TEXT, captured_privacy_epoch INTEGER,
            outcome_event_id TEXT REFERENCES events(event_id), last_error_code TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE commitments (
            commitment_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL UNIQUE REFERENCES goals(goal_id),
            accepted_event_id TEXT NOT NULL REFERENCES events(event_id),
            state TEXT NOT NULL CHECK(state IN ('ACKNOWLEDGED','IN_PROGRESS',
                'FULFILLED','EXPLAINED_FAILURE','ERASED')),
            deadline TEXT NOT NULL, scope_sha256 TEXT NOT NULL,
            result_event_id TEXT REFERENCES events(event_id), feedback_message_id TEXT,
            feedback_outcome TEXT, failure_code TEXT, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE wake_decisions (
            decision_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
            trigger_event_id TEXT REFERENCES events(event_id),
            goal_id TEXT REFERENCES goals(goal_id),
            decision TEXT NOT NULL CHECK(decision IN ('SKIP','EXPLORE','OBSERVE')),
            reason_code TEXT NOT NULL, budget_snapshot TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        "CREATE INDEX idx_goal_evidence_event ON goal_evidence(event_id)",
        "CREATE INDEX idx_goals_due ON goals(state,next_due_at)",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM goals LIMIT 1").first():
        raise RuntimeError("GOALS_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    for table in ("wake_decisions", "commitments", "goal_steps", "goal_evidence", "goals"):
        op.drop_table(table)
