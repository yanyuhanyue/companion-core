"""Source-bound Life topics and SHADOW results of existing activity jobs."""

from alembic import op

revision = "0034_life_topics"
down_revision = "0033_model_usage_job_index"


def upgrade():
    for sql in (
        """CREATE TABLE life_topics (
            topic_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations,
            source_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK(kind IN ('conversation','reading')),
            state TEXT NOT NULL CHECK(state IN ('WAITING','DUE','CLOSED','EXPIRED')),
            due_at TEXT NOT NULL, expires_at TEXT NOT NULL, reason_code TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1, decision_count INTEGER NOT NULL DEFAULT 0,
            closed_event_id TEXT REFERENCES events(event_id), created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE life_topic_sources (
            topic_id TEXT NOT NULL REFERENCES life_topics, event_id TEXT NOT NULL REFERENCES events,
            PRIMARY KEY(topic_id,event_id)
        )""",
        "CREATE INDEX idx_life_topic_source ON life_topic_sources(event_id)",
        "CREATE INDEX idx_life_topic_due ON life_topics(state,due_at)",
        """CREATE UNIQUE INDEX idx_life_active_conversation ON life_topics(conversation_id)
            WHERE kind='conversation' AND state IN ('WAITING','DUE')""",
        """CREATE TABLE life_activity_results (
            job_id TEXT PRIMARY KEY REFERENCES jobs, topic_id TEXT NOT NULL REFERENCES life_topics,
            topic_revision INTEGER NOT NULL, conversation_revision INTEGER NOT NULL,
            authority_sha256 TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            source_fingerprints_json TEXT NOT NULL,
            action TEXT, destination TEXT, reason TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            proposal_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(state IN ('PENDING','SHADOW','SKIPPED','DEFERRED','INVALIDATED')),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(topic_id,topic_revision)
        )""",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM life_topics LIMIT 1").first():
        raise RuntimeError("LIFE_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    for table in ("life_activity_results", "life_topic_sources", "life_topics"):
        op.drop_table(table)
