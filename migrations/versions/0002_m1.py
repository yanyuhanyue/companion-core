"""M1 evidence, memory, self model and erasure projection."""

import sqlite3
from pathlib import Path

from alembic import op

revision = "0002_m1"
down_revision = "0001_m0"


def upgrade():
    root = Path(__file__).resolve().parents[2]
    path = root / "db/reference-v0.3.sql"
    if not path.exists():
        path = root / "reference-v0.3.sql"
    statement = ""
    for line in path.read_text("utf-8").split("-- M1 only")[1].splitlines()[1:]:
        if line.startswith("--"):
            continue
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            op.get_bind().exec_driver_sql(statement)
            statement = ""
    for sql in (
        "ALTER TABLE events ADD COLUMN redacted INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE events ADD COLUMN memory_processed INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE memories ADD COLUMN fact_key TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE memories ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0",
        "CREATE TABLE runtime_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    ):
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    for trigger in ("memory_fts_insert", "memory_fts_update", "memory_fts_delete"):
        op.get_bind().exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
    for name in (
        "runtime_meta",
        "privacy_erasure_ledger",
        "memories_fts",
        "conversation_summaries",
        "trait_evidence",
        "traits",
        "memory_revisions",
        "memory_evidence",
        "memories",
        "episode_events",
        "episodes",
    ):
        op.drop_table(name)
    op.get_bind().exec_driver_sql("ALTER TABLE events DROP COLUMN redacted")
    op.get_bind().exec_driver_sql("ALTER TABLE events DROP COLUMN memory_processed")
