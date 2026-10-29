"""CC-STYLE distillation: a pair work queue and abstract candidates, erased with sources."""

from alembic import op

revision = "0052_style_distill"
down_revision = "0051_exploration_query"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE style_pairs (
        pair_key TEXT PRIMARY KEY,
        source_event_id TEXT NOT NULL REFERENCES events(event_id),
        video TEXT NOT NULL,
        comment_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN
            ('PENDING','RUNNING','RETRY_WAIT','DONE','NONE','REJECTED','UNKNOWN','CANCELLED',
             'FAILED')),
        attempts INTEGER NOT NULL DEFAULT 0,
        lease_until TEXT,
        next_attempt_at TEXT,
        model_request_id TEXT REFERENCES model_usage(request_id),
        pattern_sha256 TEXT,
        error_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    op.execute("CREATE INDEX idx_style_pairs_due ON style_pairs(state,next_attempt_at)")
    op.execute("CREATE INDEX idx_style_pairs_source ON style_pairs(source_event_id)")
    op.execute("""CREATE TABLE style_candidates (
        pattern_sha256 TEXT PRIMARY KEY,
        pattern_json TEXT NOT NULL,
        refs_json TEXT NOT NULL,
        disagreement INTEGER NOT NULL DEFAULT 0 CHECK(disagreement IN (0,1)),
        state TEXT NOT NULL CHECK(state IN ('CANDIDATE','APPROVED','REJECTED')),
        candidate_id TEXT,
        approved_version INTEGER,
        reviewer TEXT,
        review_reason TEXT,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK((state='APPROVED') = (approved_version IS NOT NULL AND candidate_id IS NOT NULL))
    )""")


def downgrade():
    bind = op.get_bind()
    if bind.exec_driver_sql(
        "SELECT 1 FROM style_pairs WHERE state IN ('RUNNING','UNKNOWN') LIMIT 1"
    ).fetchone():
        raise RuntimeError("Unresolved style distillation calls block downgrade")
    op.execute("DROP TABLE style_candidates")
    op.execute("DROP TABLE style_pairs")
