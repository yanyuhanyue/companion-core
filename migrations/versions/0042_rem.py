"""Private fictional REM results over original jobs and model usage."""

from alembic import op

revision = "0042_rem"
down_revision = "0041_agent_conversations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE rem_records (
        rem_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
        local_date TEXT NOT NULL,
        timezone TEXT NOT NULL,
        config_sha256 TEXT NOT NULL,
        source_memory_ids_json TEXT NOT NULL CHECK(json_valid(source_memory_ids_json)),
        source_event_ids_json TEXT NOT NULL CHECK(json_valid(source_event_ids_json)),
        source_snapshot_json TEXT NOT NULL CHECK(json_valid(source_snapshot_json)),
        output_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(output_json)),
        model_request_id TEXT REFERENCES model_usage(request_id),
        redacted INTEGER NOT NULL DEFAULT 0 CHECK(redacted IN (0,1)),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        UNIQUE(local_date,timezone)
    )""")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM rem_records LIMIT 1").first():
        raise RuntimeError("REM_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.drop_table("rem_records")
