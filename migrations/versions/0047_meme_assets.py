"""Stage source-attributed catalog assets without creating execution work."""

from alembic import op

revision = "0047_meme_assets"
down_revision = "0046_capability_delegation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE meme_assets (asset_id TEXT PRIMARY KEY,custody_sha256 TEXT NOT NULL,"
        "asset_sha256 TEXT NOT NULL,source_sha256 TEXT NOT NULL,record_sha256 TEXT NOT NULL,"
        "state TEXT NOT NULL CHECK(state IN ('STAGED','QUARANTINED','REMOVED','ERASED')),"
        "mime TEXT,data BLOB,metadata_json TEXT,"
        "UNIQUE(custody_sha256,source_sha256,asset_sha256),"
        "CHECK(state='STAGED' OR (data IS NULL AND metadata_json IS NULL AND mime IS NULL)),"
        "CHECK(state<>'STAGED' OR (data IS NOT NULL AND metadata_json IS NOT NULL "
        "AND mime IS NOT NULL)))"
    )
    op.execute(
        "CREATE TABLE meme_asset_sources ("
        "asset_id TEXT NOT NULL REFERENCES meme_assets(asset_id),"
        "event_id TEXT NOT NULL REFERENCES events(event_id),"
        "caption_job_id TEXT NOT NULL REFERENCES meme_captions(job_id),"
        "PRIMARY KEY(asset_id,event_id,caption_job_id))"
    )
    op.execute("CREATE INDEX meme_asset_source_events ON meme_asset_sources(event_id)")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM meme_assets LIMIT 1").first():
        raise RuntimeError("MEME_ASSET_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("DROP TABLE meme_asset_sources")
    op.execute("DROP TABLE meme_assets")
