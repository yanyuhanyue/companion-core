"""Owner-shared links and bounded, one-shot image interpretation."""

from alembic import op

revision = "0013_content"
down_revision = "0012_media"


def upgrade():
    op.get_bind().exec_driver_sql(
        """CREATE TABLE content_items (
            item_id TEXT PRIMARY KEY, owner_event_id TEXT NOT NULL REFERENCES events(event_id),
            kind TEXT NOT NULL CHECK(kind IN ('link','qq_image','link_image')),
            slot INTEGER NOT NULL CHECK(slot>=0 AND slot<4),
            source_event_id TEXT REFERENCES events(event_id),
            target_url TEXT NOT NULL DEFAULT '', read_request_id TEXT,
            state TEXT NOT NULL CHECK(state IN
                ('WAITING','QUEUED','FETCHING','RUNNING','SUCCEEDED','FAILED','UNKNOWN','ERASED')),
            media_sha256 TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL DEFAULT '',
            captured_privacy_epoch INTEGER NOT NULL, reserved_bytes INTEGER NOT NULL DEFAULT 0,
            usage_request_id TEXT, output_event_id TEXT REFERENCES events(event_id),
            lease_token TEXT, lease_until TEXT, deadline TEXT NOT NULL,
            error_code TEXT, redacted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, started_at TEXT, updated_at TEXT NOT NULL,
            UNIQUE(owner_event_id,kind,slot)
        )"""
    )
    op.create_index("idx_content_source", "content_items", ["source_event_id"])


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM content_items LIMIT 1").first():
        raise RuntimeError("CONTENT_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    op.drop_table("content_items")
