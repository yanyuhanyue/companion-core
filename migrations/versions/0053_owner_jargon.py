"""Owner jargon: terms with source references and a paid work queue, erased with sources."""

from alembic import op

revision = "0053_owner_jargon"
down_revision = "0052_style_distill"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE jargon_terms (
        term TEXT PRIMARY KEY,
        refs_json TEXT NOT NULL,
        uses INTEGER NOT NULL CHECK(uses>0),
        state TEXT NOT NULL CHECK(state IN ('CANDIDATE','INFERRED','NOT_JARGON','FORGOTTEN')),
        meaning TEXT,
        inferred_uses INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK((state='INFERRED') = (meaning IS NOT NULL))
    )""")
    op.execute("""CREATE TABLE jargon_work (
        work_key TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('extract','infer')),
        term TEXT,
        source_event_ids_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN
            ('PENDING','RUNNING','RETRY_WAIT','DONE','UNKNOWN','CANCELLED','FAILED')),
        attempts INTEGER NOT NULL DEFAULT 0,
        lease_until TEXT,
        next_attempt_at TEXT,
        model_request_id TEXT REFERENCES model_usage(request_id),
        error_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK((kind='infer') = (term IS NOT NULL))
    )""")
    op.execute("CREATE INDEX idx_jargon_work_due ON jargon_work(state,next_attempt_at)")


def downgrade():
    bind = op.get_bind()
    if bind.exec_driver_sql(
        "SELECT 1 FROM jargon_work WHERE state IN ('RUNNING','UNKNOWN') LIMIT 1"
    ).fetchone():
        raise RuntimeError("Unresolved jargon calls block downgrade")
    op.execute("DROP TABLE jargon_work")
    op.execute("DROP TABLE jargon_terms")
