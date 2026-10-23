"""Keep Reader lease authority and accounting after content erasure."""

from alembic import op

revision = "0024_reading_attempts"
down_revision = "0023_meme_discovery_reservations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE reading_attempts ("
        "request_id TEXT PRIMARY KEY REFERENCES exploration_requests(request_id),"
        "adapter_id TEXT NOT NULL,lease_token TEXT NOT NULL UNIQUE,lease_until TEXT NOT NULL,"
        "context_json TEXT NOT NULL,allowed_hosts_json TEXT NOT NULL,"
        "original_url_sha256 TEXT NOT NULL,source_grant_json TEXT NOT NULL DEFAULT 'null',"
        "state TEXT NOT NULL CHECK(state IN ('RUNNING','DONE','FAILED','UNKNOWN','CANCELLED')),"
        "result_sha256 TEXT,result_event_ids_json TEXT NOT NULL DEFAULT '[]',"
        "observed_requests INTEGER NOT NULL DEFAULT 0 CHECK(observed_requests>=0),"
        "observed_bytes INTEGER NOT NULL DEFAULT 0 CHECK(observed_bytes>=0),"
        "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
    )


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM reading_attempts LIMIT 1").first():
        raise RuntimeError("READING_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("DROP TABLE reading_attempts")
