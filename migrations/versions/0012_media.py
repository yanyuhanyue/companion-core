"""Bounded media sessions and durable, non-retryable media request reservations."""

from alembic import op

revision = "0012_media"
down_revision = "0011_executive"


def upgrade():
    for sql in (
        """CREATE TABLE media_sessions (
            session_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
            consent_id TEXT NOT NULL REFERENCES consents(consent_id),
            token_hash TEXT NOT NULL, modalities_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('OPEN','CLOSED')),
            captured_privacy_epoch INTEGER NOT NULL, expires_at TEXT NOT NULL,
            bytes_reserved INTEGER NOT NULL DEFAULT 0, max_bytes INTEGER NOT NULL,
            close_reason TEXT, created_at TEXT NOT NULL, closed_at TEXT
        )""",
        """CREATE TABLE media_requests (
            request_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES media_sessions(session_id),
            request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, operation TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('RESERVED','SUCCEEDED','UNKNOWN')),
            reserved_bytes INTEGER NOT NULL, usage_request_id TEXT,
            event_id TEXT REFERENCES events(event_id), created_at TEXT NOT NULL,
            UNIQUE(session_id,request_key)
        )""",
        "CREATE INDEX idx_media_sessions_date ON media_sessions(created_at)",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM media_sessions LIMIT 1").first():
        raise RuntimeError("MEDIA_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    op.drop_table("media_requests")
    op.drop_table("media_sessions")
