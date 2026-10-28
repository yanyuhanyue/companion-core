"""Synthetic evaluation plans keep one result row for each of up to 216 requests."""

from alembic import op

revision = "0049_evaluation_ordinals"
down_revision = "0048_meme_vectors"
branch_labels = None
depends_on = None

COLUMNS = ("request_id,evaluation_id,ordinal,text,text_redacted,finish_reason,evidence_type,"
           "created_at")


def _rebuild(limit):
    # SQLite cannot alter a CHECK in place; copy rows into the same shape.
    op.execute(f"""CREATE TABLE evaluation_results_next (
        request_id TEXT PRIMARY KEY REFERENCES model_usage(request_id),
        evaluation_id TEXT NOT NULL REFERENCES evaluation_runs(evaluation_id),
        ordinal INTEGER NOT NULL CHECK(ordinal>=0 AND ordinal<{limit}),
        text TEXT NOT NULL,
        text_redacted INTEGER NOT NULL CHECK(text_redacted IN (0,1)),
        finish_reason TEXT,
        evidence_type TEXT NOT NULL CHECK(evidence_type IN
            ('MODEL_RESPONSE','SYNTHETIC_MODEL_RESPONSE')),
        created_at TEXT NOT NULL,
        UNIQUE(evaluation_id,ordinal)
    )""")
    op.execute(f"INSERT INTO evaluation_results_next ({COLUMNS}) "
               f"SELECT {COLUMNS} FROM evaluation_results")
    op.execute("DROP TABLE evaluation_results")
    op.execute("ALTER TABLE evaluation_results_next RENAME TO evaluation_results")


def upgrade():
    _rebuild(216)


def downgrade():
    # Refuse to drop results that the original six-request pilot schema cannot hold.
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT 1 FROM evaluation_results WHERE ordinal>=6 LIMIT 1").first():
        raise RuntimeError("synthetic evaluation results exceed the pilot schema")
    _rebuild(6)
