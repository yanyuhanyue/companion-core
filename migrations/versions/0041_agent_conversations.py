"""Bind native SDK conversations and tool outcomes to existing jobs and accounting."""

from alembic import op

revision = "0041_agent_conversations"
down_revision = "0040_owner_authority"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE agent_conversations ("
               "job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,"
               "phase TEXT NOT NULL CHECK(phase IN "
               "('READY','MODEL_PENDING','WAITING','OUTPUT','COMPLETE','STOPPED')),"
               "conversation_json TEXT,snapshots_json TEXT NOT NULL DEFAULT '[]',"
               "request_id TEXT REFERENCES model_usage(request_id),output_text TEXT,"
               "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
    op.execute("CREATE TABLE agent_tool_calls ("
               "job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,"
               "tool_call_id TEXT NOT NULL,tool_name TEXT NOT NULL,args_sha256 TEXT NOT NULL,"
               "model_request_id TEXT NOT NULL REFERENCES model_usage(request_id),"
               "state TEXT NOT NULL CHECK(state IN ('STARTED','RETURNED','DEFERRED','UNKNOWN')),"
               "result_json TEXT,metadata_json TEXT,"
               "request_id TEXT REFERENCES exploration_requests(request_id),"
               "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
               "PRIMARY KEY(job_id,tool_call_id))")
    op.execute("CREATE INDEX idx_agent_tool_reader ON agent_tool_calls(request_id)")
    op.execute("DELETE FROM runtime_meta WHERE key LIKE 'reading_followup:%'")
    op.execute("CREATE TABLE captured_images ("
               "item_id TEXT PRIMARY KEY REFERENCES content_items(item_id) ON DELETE CASCADE,"
               "proof_json TEXT NOT NULL,mime TEXT NOT NULL,expires_at TEXT NOT NULL)")
    op.execute("CREATE TABLE agent_image_inputs ("
               "job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,"
               "item_id TEXT NOT NULL REFERENCES captured_images(item_id) ON DELETE CASCADE,"
               "position INTEGER NOT NULL CHECK(position>=0),PRIMARY KEY(job_id,item_id),"
               "UNIQUE(job_id,position))")
    # Keep already captured originals within their existing signed window and
    # recorded expiry. No bytes, lease, approval or expiry is synthesized.
    op.execute("INSERT OR IGNORE INTO captured_images(item_id,proof_json,mime,expires_at) "
               "SELECT json_extract(i.value,'$.item_id'),json_extract(i.value,'$.upload_proof'),"
               "json_extract(i.value,'$.upload_proof.mime'),"
               "json_extract(r.value,'$.original_expires_at') FROM runtime_meta r,"
               "json_each(r.value,'$.images') i JOIN content_items c "
               "ON c.item_id=json_extract(i.value,'$.item_id') "
               "WHERE r.key LIKE 'live_text_image:%' AND c.redacted=0 "
               "AND json_type(i.value,'$.upload_proof')='object' "
               "AND json_type(r.value,'$.original_expires_at')='text'")


def downgrade():
    # Images may be captured before a conversation exists. Every native custody
    # record must survive until an explicit review/export/erasure permits removal.
    for table in ("agent_conversations", "agent_tool_calls", "captured_images",
                  "agent_image_inputs"):
        if op.get_bind().exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
            raise RuntimeError("AGENT_CONVERSATION_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("DROP TABLE agent_image_inputs")
    op.execute("DROP TABLE captured_images")
    op.execute("DROP TABLE agent_tool_calls")
    op.execute("DROP TABLE agent_conversations")
