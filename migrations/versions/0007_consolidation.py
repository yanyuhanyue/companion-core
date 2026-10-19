"""Recoverable daily consolidation and evidence-bound reflection proposals."""

from alembic import op

revision = "0007_consolidation"
down_revision = "0006_affect"


def upgrade():
    for sql in (
        """CREATE TABLE reflection_runs (
            run_id TEXT PRIMARY KEY, identity_id TEXT NOT NULL REFERENCES principals(actor_id),
            local_date TEXT NOT NULL, timezone TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN
              ('RUNNING','COMMITTED','SKIPPED','FAILED','CANCELLED')),
            source_set_sha256 TEXT NOT NULL, captured_privacy_epoch INTEGER NOT NULL,
            lease_token TEXT, lease_until TEXT,
            model_request_id TEXT REFERENCES model_usage(request_id),
            last_error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(identity_id,local_date,timezone)
        )""",
        """CREATE TABLE reflection_evidence (
            run_id TEXT NOT NULL REFERENCES reflection_runs(run_id),
            event_id TEXT NOT NULL REFERENCES events(event_id),
            source_id TEXT NOT NULL, outbox_message_id TEXT REFERENCES outbox(message_id),
            PRIMARY KEY(run_id,source_id)
        )""",
        """CREATE TABLE reflection_suggestions (
            suggestion_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES reflection_runs(run_id),
            kind TEXT NOT NULL, content TEXT NOT NULL, evidence_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN
              ('PENDING','ACCEPT_AS_HYPOTHESIS','REJECT','NEED_MORE_EVIDENCE','REDACTED')),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE consolidation_runs (
            run_id TEXT PRIMARY KEY, identity_id TEXT NOT NULL REFERENCES principals(actor_id),
            local_date TEXT NOT NULL, timezone TEXT NOT NULL, config_version TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN
              ('SCHEDULED','LEASED','PARTIAL','COMPLETE','SKIPPED','FAILED','CANCELLED')),
            steps_json TEXT NOT NULL, source_set_sha256 TEXT NOT NULL DEFAULT '',
            captured_privacy_epoch INTEGER NOT NULL, lease_token TEXT, lease_until TEXT,
            next_attempt_at TEXT, last_error_code TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(identity_id,local_date,timezone,config_version)
        )""",
        """CREATE TABLE consolidation_evidence (
            run_id TEXT NOT NULL REFERENCES consolidation_runs(run_id),
            event_id TEXT NOT NULL REFERENCES events(event_id), PRIMARY KEY(run_id,event_id)
        )""",
        "CREATE INDEX idx_reflection_evidence_event ON reflection_evidence(event_id)",
        "CREATE INDEX idx_consolidation_evidence_event ON consolidation_evidence(event_id)",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    for table in ("reflection_runs", "consolidation_runs"):
        if op.get_bind().exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
            raise RuntimeError("CONSOLIDATION_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    for table in (
        "consolidation_evidence",
        "consolidation_runs",
        "reflection_suggestions",
        "reflection_evidence",
        "reflection_runs",
    ):
        op.drop_table(table)
