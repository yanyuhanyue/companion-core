"""Bind bounded public-image captions to native jobs and original model accounting."""

from alembic import op

revision = "0025_meme_captions"
down_revision = "0024_reading_attempts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE meme_captions ("
        "job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),"
        "cache_key TEXT NOT NULL,request_sha256 TEXT NOT NULL UNIQUE,"
        "asset_sha256 TEXT NOT NULL,source_sha256 TEXT NOT NULL,"
        "identity_json TEXT NOT NULL,context_json TEXT NOT NULL,source_json TEXT NOT NULL,"
        "request_json TEXT NOT NULL,asset_data BLOB,result_json TEXT,"
        "usage_request_id TEXT,reuse_job_id TEXT REFERENCES meme_captions(job_id),"
        "plan_sha256 TEXT NOT NULL,privacy_epoch INTEGER NOT NULL,"
        "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
    )
    op.execute("CREATE INDEX meme_captions_cache ON meme_captions(cache_key)")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM meme_captions LIMIT 1").first():
        raise RuntimeError("MEME_CAPTION_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("DROP TABLE meme_captions")
