"""Watch ledger also judges completed goal web reads and keeps owner withdrawals."""

from alembic import op

revision = "0056_watch_ledger_goal_reads"
down_revision = "0055_runtime_configuration"
branch_labels = None
depends_on = None

COLUMNS = ("entry_id,source_event_id,platform,item_ref,depth,state,attempts,lease_until,"
           "next_attempt_at,model_request_id,rating,take,topic_tags_json,error_code,"
           "created_at,updated_at")


def _rebuild(platforms, states):
    # SQLite cannot alter a CHECK in place; copy rows into the same shape.
    op.execute(f"""CREATE TABLE watch_ledger_next (
        entry_id TEXT PRIMARY KEY,
        source_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        platform TEXT NOT NULL CHECK(platform IN ({platforms})),
        item_ref TEXT NOT NULL UNIQUE,
        depth TEXT NOT NULL CHECK(depth IN
            ('title_only','text','subtitle','comments','media_sampled')),
        state TEXT NOT NULL CHECK(state IN ({states})),
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
    op.execute(f"INSERT INTO watch_ledger_next ({COLUMNS}) SELECT {COLUMNS} FROM watch_ledger")
    op.execute("DROP TABLE watch_ledger")
    op.execute("ALTER TABLE watch_ledger_next RENAME TO watch_ledger")
    op.execute("CREATE INDEX idx_watch_ledger_due ON watch_ledger(state,next_attempt_at)")


STATES = ("'PENDING','RUNNING','RETRY_WAIT','JUDGED','SKIPPED','UNKNOWN','CANCELLED',"
          "'FAILED'")


def upgrade():
    _rebuild("'bilibili','heybox','web'", STATES + ",'WITHDRAWN'")


def downgrade():
    # The older schema cannot hold web entries or an owner withdrawal; dropping them would
    # let a withdrawn judgement be made again, so refuse instead.
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM watch_ledger WHERE platform='web' OR state='WITHDRAWN' LIMIT 1"
    ).first():
        raise RuntimeError("web or withdrawn watch ledger entries block downgrade")
    _rebuild("'bilibili','heybox'", STATES)
