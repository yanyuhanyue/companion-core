"""Watch ledger: one judgement per completed Bilibili/Heybox read, erased with its source."""

from alembic import op

revision = "0050_watch_ledger"
down_revision = "0049_evaluation_ordinals"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE watch_ledger (
        entry_id TEXT PRIMARY KEY,
        source_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        platform TEXT NOT NULL CHECK(platform IN ('bilibili','heybox')),
        item_ref TEXT NOT NULL UNIQUE,
        depth TEXT NOT NULL CHECK(depth IN
            ('title_only','text','subtitle','comments','media_sampled')),
        state TEXT NOT NULL CHECK(state IN
            ('PENDING','RUNNING','RETRY_WAIT','JUDGED','SKIPPED','UNKNOWN','CANCELLED',
             'FAILED')),
        attempts INTEGER NOT NULL DEFAULT 0,
        lease_until TEXT,
        next_attempt_at TEXT,
        model_request_id TEXT REFERENCES model_usage(request_id),
        rating INTEGER CHECK(rating IS NULL OR rating BETWEEN -2 AND 2),
        take TEXT CHECK(take IS NULL OR length(take)<=60),
        topic_tags_json TEXT NOT NULL DEFAULT '[]',
        error_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    op.execute("CREATE INDEX idx_watch_ledger_due ON watch_ledger(state,next_attempt_at)")


def downgrade():
    # Judgements are derived and erasable; refuse only while a paid call is unresolved.
    bind = op.get_bind()
    if bind.exec_driver_sql(
        "SELECT 1 FROM watch_ledger WHERE state IN ('RUNNING','UNKNOWN') LIMIT 1"
    ).fetchone():
        raise RuntimeError("Unresolved watch ledger calls block downgrade")
    op.execute("DROP TABLE watch_ledger")
