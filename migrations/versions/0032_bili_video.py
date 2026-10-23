"""Bind one bounded video child and its phase receipts to native Reader work."""

from alembic import op

revision = "0032_bili_video"
down_revision = "0031_voice_nodes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE bili_video_items ("
        "item_id TEXT PRIMARY KEY REFERENCES content_items(item_id),"
        "request_id TEXT NOT NULL UNIQUE REFERENCES exploration_requests(request_id),"
        "source_event_id TEXT NOT NULL REFERENCES events(event_id),"
        "owner_event_id TEXT NOT NULL REFERENCES events(event_id),"
        "job_id TEXT NOT NULL REFERENCES jobs(job_id),adapter_id TEXT NOT NULL,"
        "source_sha256 TEXT NOT NULL,source_snapshot_sha256 TEXT NOT NULL,"
        "metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),"
        "source_grant_json TEXT NOT NULL DEFAULT 'null' CHECK(json_valid(source_grant_json)),"
        "source_grant_sha256 TEXT NOT NULL,"
        "policy_sha256 TEXT NOT NULL,window_sha256 TEXT NOT NULL,"
        "binding_sha256 TEXT NOT NULL,captured_privacy_epoch INTEGER NOT NULL,"
        "deadline TEXT NOT NULL,"
        "state TEXT NOT NULL CHECK(state IN ('DECLARED','PREPARED','ANALYZED','COMPLETE',"
        "'FAILED','UNKNOWN','CANCELLED')),"
        "lease_sha256 TEXT NOT NULL DEFAULT '',"
        "lease_token_sha256 TEXT NOT NULL DEFAULT '',"
        "lease_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(lease_json)),"
        "target_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(target_json)),"
        "plan_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(plan_json)),"
        "download_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(download_json)),"
        "output_event_id TEXT UNIQUE,result_sha256 TEXT,"
        "redacted INTEGER NOT NULL DEFAULT 0 CHECK(redacted IN (0,1)),"
        "last_error_code TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
    )
    op.execute("CREATE INDEX idx_bili_video_source ON bili_video_items(source_event_id)")
    op.execute("CREATE INDEX idx_bili_video_owner ON bili_video_items(owner_event_id)")
    op.execute(
        "CREATE TABLE bili_video_phases ("
        "request_id TEXT NOT NULL REFERENCES bili_video_items(request_id),"
        "phase TEXT NOT NULL CHECK(phase IN ('prepare','preview','clips')),"
        "request_sha256 TEXT NOT NULL,model_request_id TEXT UNIQUE,payload_sha256 TEXT,"
        "model_job_id TEXT REFERENCES jobs(job_id),"
        "state TEXT NOT NULL CHECK(state IN "
        "('PENDING','SETTLED','REJECTED','UNKNOWN','CANCELLED')),"
        "evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),"
        "analysis_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(analysis_json)),"
        "response_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(response_json)),"
        "received_bytes INTEGER NOT NULL DEFAULT 0 CHECK(received_bytes>=0),"
        "last_error_code TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
        "PRIMARY KEY(request_id,phase),"
        "CHECK((phase='prepare' AND model_request_id IS NULL AND model_job_id IS NULL) OR "
        "(phase<>'prepare' AND model_request_id IS NOT NULL AND model_job_id IS NOT NULL)))"
    )


def downgrade():
    connection = op.get_bind()
    if connection.exec_driver_sql("SELECT 1 FROM bili_video_items LIMIT 1").first():
        raise RuntimeError("BILI_VIDEO_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("DROP TABLE bili_video_phases")
    op.execute("DROP TABLE bili_video_items")
