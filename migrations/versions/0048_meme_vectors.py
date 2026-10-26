"""Rebuildable vectors over exact staged assets and settled native captions."""

from alembic import op

revision = "0048_meme_vectors"
down_revision = "0047_meme_assets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE meme_vectors (
            cache_key TEXT PRIMARY KEY
                CHECK(length(cache_key)=64 AND cache_key NOT GLOB '*[^0-9a-f]*'),
            asset_id TEXT NOT NULL REFERENCES meme_assets(asset_id) CHECK(length(asset_id)=64),
            caption_job_id TEXT NOT NULL REFERENCES meme_captions(job_id)
                CHECK(length(caption_job_id)=36),
            asset_sha256 TEXT NOT NULL CHECK(length(asset_sha256)=64),
            custody_sha256 TEXT NOT NULL CHECK(length(custody_sha256)=64),
            source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
            record_sha256 TEXT NOT NULL CHECK(length(record_sha256)=64),
            caption_identity_sha256 TEXT NOT NULL CHECK(length(caption_identity_sha256)=64),
            caption_sha256 TEXT NOT NULL CHECK(length(caption_sha256)=64),
            model_version TEXT NOT NULL
                CHECK(length(CAST(model_version AS BLOB)) BETWEEN 1 AND 160),
            dimensions INTEGER NOT NULL CHECK(typeof(dimensions)='integer' AND dimensions=512),
            vector_sha256 TEXT NOT NULL CHECK(length(vector_sha256)=64),
            vector BLOB NOT NULL CHECK(typeof(vector)='blob' AND length(vector)=2048),
            UNIQUE(asset_id,caption_job_id)
        )
    """)
    op.execute("CREATE INDEX meme_vectors_caption ON meme_vectors(caption_job_id)")


def downgrade():
    op.execute("DROP TABLE meme_vectors")
