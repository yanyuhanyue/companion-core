"""Bound non-model discovery resources on existing durable jobs."""

from alembic import op

revision = "0023_meme_discovery_reservations"
down_revision = "0022_input_reply_revision"
branch_labels = None
depends_on = None


def upgrade():
    counters = ",".join(
        f"{prefix}{name} INTEGER NOT NULL DEFAULT 0 CHECK({prefix}{name}>=0)"
        for name in ("queries", "requests", "download_bytes", "storage_bytes", "model_cost")
        for prefix in ("reserved_", "")
    )
    op.execute("CREATE TABLE meme_discovery_reservations ("
               "job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),attempt_key TEXT NOT NULL UNIQUE,"
               "day TEXT NOT NULL,state TEXT NOT NULL "
               "CHECK(state IN ('RESERVED','SETTLED','UNKNOWN')),"
               + counters + ",created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
    op.execute("CREATE INDEX meme_discovery_day ON meme_discovery_reservations(day)")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM meme_discovery_reservations LIMIT 1").first():
        raise RuntimeError("MEME_BUDGET_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("DROP TABLE meme_discovery_reservations")
