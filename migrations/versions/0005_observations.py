"""Authenticated observations, source dependencies and bounded device consent."""

from alembic import op

revision = "0005_observations"
down_revision = "0004_diary"


def upgrade():
    for sql in (
        """CREATE TABLE consents (
            consent_id TEXT PRIMARY KEY, capabilities_json TEXT NOT NULL,
            device_id TEXT NOT NULL, session_id TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT
        )""",
        """CREATE TABLE observations (
            event_id TEXT PRIMARY KEY REFERENCES events(event_id),
            adapter_id TEXT NOT NULL, source_kind TEXT NOT NULL,
            result_status TEXT NOT NULL CHECK(result_status IN
                ('DISCOVERED','FETCHED','WATCHED','FAILED','UNKNOWN')),
            source_sha256 TEXT NOT NULL, canonical_url TEXT NOT NULL,
            device_id TEXT, session_id TEXT, consent_id TEXT REFERENCES consents(consent_id),
            evidence_json TEXT NOT NULL, captured_privacy_epoch INTEGER NOT NULL,
            redacted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE event_dependencies (
            event_id TEXT NOT NULL REFERENCES events(event_id),
            source_event_id TEXT NOT NULL REFERENCES events(event_id),
            PRIMARY KEY(event_id,source_event_id), CHECK(event_id<>source_event_id)
        )""",
        "CREATE INDEX idx_event_dependencies_source ON event_dependencies(source_event_id)",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    connection = op.get_bind()
    for table in ("observations", "consents"):
        if connection.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
            raise RuntimeError("OBSERVATION_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    for table in ("event_dependencies", "observations", "consents"):
        op.drop_table(table)
