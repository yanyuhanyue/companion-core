"""Evidence-backed, expiring interaction preferences."""

from alembic import op

revision = "0006_affect"
down_revision = "0005_observations"


def upgrade():
    for sql in (
        """CREATE TABLE affect_states (
            identity_id TEXT PRIMARY KEY REFERENCES principals(actor_id),
            state_json TEXT NOT NULL, confidence REAL NOT NULL,
            updated_at TEXT NOT NULL, expires_at TEXT,
            version INTEGER NOT NULL DEFAULT 1
        )""",
        """CREATE TABLE affect_evidence (
            identity_id TEXT NOT NULL REFERENCES principals(actor_id),
            event_id TEXT NOT NULL REFERENCES events(event_id),
            dimension TEXT NOT NULL, prior_state TEXT NOT NULL, new_state TEXT NOT NULL,
            reason_code TEXT NOT NULL, confidence REAL NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            PRIMARY KEY(identity_id,event_id,dimension)
        )""",
        "CREATE INDEX idx_affect_evidence_event ON affect_evidence(event_id)",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM affect_evidence LIMIT 1").first():
        raise RuntimeError("AFFECT_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    op.drop_table("affect_evidence")
    op.drop_table("affect_states")
