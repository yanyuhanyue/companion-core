"""Add bounded local voice job custody without adding a delivery queue."""

from alembic import op

revision = "0031_voice_nodes"
down_revision = "0030_private_poke"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE voice_jobs ("
        "job_id TEXT PRIMARY KEY,operation TEXT NOT NULL CHECK(operation IN ('tts','asr')),"
        "event_id TEXT NOT NULL REFERENCES events(event_id),"
        "native_source_event_id TEXT NOT NULL,"
        "conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),"
        "message_id TEXT REFERENCES outbox(message_id),source_segment_index INTEGER,"
        "source_sha256 TEXT NOT NULL,source_input_sha256 TEXT,source_proof_sha256 TEXT,"
        "identity_sha256 TEXT NOT NULL,window_sha256 TEXT NOT NULL,"
        "captured_privacy_epoch INTEGER NOT NULL,captured_reply_revision INTEGER NOT NULL,"
        "outbox_content_sha256 TEXT,outbox_delivery_key TEXT,"
        "node_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,config_sha256 TEXT NOT NULL,"
        "config_json TEXT NOT NULL CHECK(json_valid(config_json)),"
        "generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>=1),"
        "state TEXT NOT NULL CHECK(state IN ('QUEUED','LEASED','RUNNING','READY','TRANSCRIBED',"
        "'CANCELLED','EXPIRED','UNKNOWN','FAILED')),"
        "input_mime TEXT NOT NULL CHECK(input_mime IN ('text/plain','audio/wav')),"
        "input_sha256 TEXT NOT NULL,input_bytes INTEGER NOT NULL CHECK(input_bytes>0),"
        "input_duration_seconds REAL,"
        "source_partial INTEGER NOT NULL CHECK(source_partial IN (0,1)),"
        "input_blob BLOB,result_blob BLOB,result_sha256 TEXT,result_bytes INTEGER,"
        "lease_token_sha256 TEXT,"
        "lease_json TEXT CHECK(lease_json IS NULL OR json_valid(lease_json)),"
        "lease_issued_at TEXT,lease_until TEXT,started_at TEXT,completed_at TEXT,"
        "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deadline_at TEXT NOT NULL,"
        "result_expires_at TEXT,last_error_code TEXT,"
        "CHECK((operation='tts' AND message_id IS NOT NULL AND source_segment_index IS NULL) "
        "OR (operation='asr' AND message_id IS NULL AND source_segment_index IS NOT NULL "
        "AND source_segment_index BETWEEN 0 AND 63)))"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_voice_tts_message ON voice_jobs(message_id) WHERE operation='tts'"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_voice_asr_source ON voice_jobs(event_id,source_segment_index) "
        "WHERE operation='asr'"
    )
    op.execute("CREATE INDEX idx_voice_queue ON voice_jobs(node_id,state,created_at)")
    op.execute("CREATE INDEX idx_voice_source ON voice_jobs(event_id,conversation_id)")
    op.execute(
        "CREATE TABLE voice_claims (node_id TEXT NOT NULL,request_id TEXT NOT NULL,"
        "request_sha256 TEXT NOT NULL,response_json TEXT NOT NULL CHECK(json_valid(response_json)),"
        "job_id TEXT REFERENCES voice_jobs(job_id),created_at TEXT NOT NULL,"
        "PRIMARY KEY(node_id,request_id))"
    )
    op.execute(
        "CREATE TABLE voice_node_state (node_id TEXT PRIMARY KEY,"
        "active_job_id TEXT REFERENCES voice_jobs(job_id),"
        "resource_state TEXT NOT NULL CHECK(resource_state IN ('READY','RUNNING','QUARANTINED')),"
        "last_seen_at TEXT NOT NULL,last_error_code TEXT)"
    )


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM voice_jobs LIMIT 1").first():
        raise RuntimeError("VOICE_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    op.execute("DROP TABLE voice_node_state")
    op.execute("DROP TABLE voice_claims")
    op.execute("DROP TABLE voice_jobs")
