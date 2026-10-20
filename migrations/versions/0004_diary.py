"""Private daily diary runs and complete source provenance."""

from alembic import op

revision = "0004_diary"
down_revision = "0003_memory_index"


def upgrade():
    statements = [
        """CREATE TABLE diary_runs (
            run_id TEXT PRIMARY KEY,
            identity_id TEXT NOT NULL REFERENCES principals(actor_id),
            local_date TEXT NOT NULL, timezone TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN
              ('PENDING','RUNNING','RETRY_WAIT','COMMITTED','SKIPPED','FAILED','CANCELLED')),
            source_set_sha256 TEXT, captured_privacy_epoch INTEGER,
            lease_owner TEXT, lease_token TEXT, lease_until TEXT, next_attempt_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL,
            model_request_id TEXT REFERENCES model_usage(request_id), last_error_code TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(identity_id,local_date,timezone)
        )""",
        "CREATE INDEX idx_diary_runs_claim ON diary_runs(state,next_attempt_at,lease_until)",
        """CREATE TABLE diary_entries (
            entry_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES diary_runs(run_id),
            identity_id TEXT NOT NULL REFERENCES principals(actor_id),
            local_date TEXT NOT NULL, timezone TEXT NOT NULL,
            title TEXT NOT NULL, body_md TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('COMMITTED','REDACTED','DELETED')),
            privacy_scope TEXT NOT NULL DEFAULT 'private' CHECK(privacy_scope='private'),
            source_set_sha256 TEXT NOT NULL, captured_privacy_epoch INTEGER NOT NULL,
            generator_version TEXT NOT NULL, model_name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(identity_id,local_date,timezone)
        )""",
        """CREATE TABLE diary_evidence (
            entry_id TEXT NOT NULL REFERENCES diary_entries(entry_id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            event_id TEXT NOT NULL REFERENCES events(event_id),
            outbox_message_id TEXT REFERENCES outbox(message_id),
            excerpt_sha256 TEXT NOT NULL,
            PRIMARY KEY(entry_id,source_id)
        )""",
        "CREATE INDEX idx_diary_evidence_event ON diary_evidence(event_id)",
        """CREATE TABLE diary_revisions (
            revision_id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL REFERENCES diary_entries(entry_id) ON DELETE CASCADE,
            prior_title TEXT NOT NULL, prior_body_md TEXT NOT NULL,
            reason TEXT NOT NULL, changed_by TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
    ]
    for statement in statements:
        op.get_bind().exec_driver_sql(statement)


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM diary_entries LIMIT 1").first():
        raise RuntimeError("DIARY_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    for name in ("diary_revisions", "diary_evidence", "diary_entries", "diary_runs"):
        op.drop_table(name)
