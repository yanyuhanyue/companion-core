"""M0 event, job, delivery and budget contracts."""

import sqlite3
from pathlib import Path

from alembic import op

revision = "0001_m0"
down_revision = None


def upgrade():
    root = Path(__file__).resolve().parents[2]
    path = root / "db/reference-v0.3.sql"
    if not path.exists():
        path = root / "reference-v0.3.sql"
    statement = ""
    for line in path.read_text("utf-8").split("-- M1 only")[0].splitlines():
        if line.startswith("--") or line.startswith("PRAGMA"):
            continue
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            op.get_bind().exec_driver_sql(statement)
            statement = ""
    for sql in (
        "ALTER TABLE events ADD COLUMN idempotency_key TEXT",
        "ALTER TABLE events ADD COLUMN fingerprint TEXT",
        "CREATE UNIQUE INDEX idx_event_idempotency ON events(idempotency_key)",
        "ALTER TABLE jobs ADD COLUMN lease_token TEXT",
        "ALTER TABLE outbox ADD COLUMN lease_token TEXT",
        "ALTER TABLE outbox ADD COLUMN next_attempt_at TEXT",
        "ALTER TABLE model_usage ADD COLUMN state TEXT NOT NULL DEFAULT 'RESERVED'",
        "ALTER TABLE model_usage ADD COLUMN pricing_known INTEGER NOT NULL DEFAULT 0",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    for name in (
        "audit_log",
        "model_usage",
        "approvals",
        "outbox",
        "jobs",
        "events",
        "conversations",
        "principals",
    ):
        op.drop_table(name)
