"""Attribute account evidence without copying credentials or guessing old grants."""

from alembic import op

revision = "0016_source_grants"
down_revision = "0015_article_images"


def upgrade():
    for sql in (
        "CREATE TABLE source_grants (grant_id TEXT PRIMARY KEY,adapter_id TEXT NOT NULL,"
        "source TEXT NOT NULL,revoked_at TEXT,forgotten_at TEXT,created_at TEXT NOT NULL)",
        "CREATE TABLE source_grant_revisions (grant_id TEXT NOT NULL REFERENCES source_grants,"
        "epoch INTEGER NOT NULL CHECK(epoch>0),fingerprint TEXT NOT NULL,expires_at TEXT NOT NULL,"
        "created_at TEXT NOT NULL,PRIMARY KEY(grant_id,epoch))",
        "CREATE TABLE source_grant_events (event_id TEXT PRIMARY KEY REFERENCES events,"
        "grant_id TEXT NOT NULL,grant_epoch INTEGER NOT NULL,"
        "FOREIGN KEY(grant_id,grant_epoch) REFERENCES source_grant_revisions(grant_id,epoch))",
        "CREATE INDEX source_grant_event_lookup ON source_grant_events(grant_id)",
        "ALTER TABLE exploration_requests ADD COLUMN source_grant_id TEXT",
        "ALTER TABLE exploration_requests ADD COLUMN source_grant_epoch INTEGER",
        "CREATE TABLE source_grant_legacy_review (event_id TEXT PRIMARY KEY REFERENCES events,"
        "reason TEXT NOT NULL)",
        "INSERT INTO source_grant_legacy_review SELECT o.event_id,"
        "'ACCOUNT_OR_OPAQUE_SOURCE_ATTRIBUTION_UNPROVEN' FROM observations o JOIN events e "
        "USING(event_id) WHERE o.redacted=0 AND e.redacted=0 AND "
        "e.source IN ('observation.bilibili','observation.heybox') AND ("
        "json_extract(o.evidence_json,'$.reading.method')='account_api' OR "
        "json_extract(o.evidence_json,'$.article.method')='source_api' OR "
        "(json_extract(o.evidence_json,'$.reading.method') IS NULL AND "
        "json_extract(o.evidence_json,'$.article.method') IS NULL))",
    ):
        op.execute(sql)


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM source_grants LIMIT 1").first():
        raise RuntimeError("SOURCE_GRANTS_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    op.execute("DROP TABLE source_grant_legacy_review")
    op.execute("ALTER TABLE exploration_requests DROP COLUMN source_grant_epoch")
    op.execute("ALTER TABLE exploration_requests DROP COLUMN source_grant_id")
    op.execute("DROP TABLE source_grant_events")
    op.execute("DROP TABLE source_grant_revisions")
    op.execute("DROP TABLE source_grants")
