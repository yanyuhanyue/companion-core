"""Bounded read requests and owner-only private share candidates."""

from alembic import op

revision = "0009_exploration"
down_revision = "0008_goals"


def upgrade():
    for sql in (
        """CREATE TABLE exploration_requests (
            request_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
            adapter_id TEXT NOT NULL,
            operation TEXT NOT NULL CHECK(operation IN ('discover','read')),
            url TEXT NOT NULL, goal_step_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL CHECK(state IN
                ('QUEUED','RUNNING','DONE','FAILED','UNKNOWN','CANCELLED')),
            lease_token TEXT, lease_until TEXT, captured_privacy_epoch INTEGER NOT NULL,
            reserved_bytes INTEGER NOT NULL DEFAULT 0, used_bytes INTEGER,
            result_sha256 TEXT, last_error_code TEXT, created_at TEXT NOT NULL, started_at TEXT,
            updated_at TEXT NOT NULL, redacted INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE exploration_evidence (
            request_id TEXT NOT NULL REFERENCES exploration_requests(request_id),
            event_id TEXT NOT NULL REFERENCES events(event_id), PRIMARY KEY(request_id,event_id)
        )""",
        """CREATE TABLE social_candidates (
            candidate_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
            canonical_url TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            comparison_text TEXT NOT NULL, state TEXT NOT NULL,
            message_id TEXT REFERENCES outbox(message_id), reason_code TEXT,
            expires_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        "CREATE INDEX idx_social_hash ON social_candidates(content_sha256)",
        "CREATE INDEX idx_exploration_evidence_event ON exploration_evidence(event_id)",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    for table in ("exploration_requests", "social_candidates"):
        if op.get_bind().exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
            raise RuntimeError("EXPLORATION_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    for table in ("social_candidates", "exploration_evidence", "exploration_requests"):
        op.drop_table(table)
