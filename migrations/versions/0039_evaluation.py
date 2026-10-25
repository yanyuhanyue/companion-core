"""Source-bound evaluation projections over native jobs, consents and usage."""

from alembic import op

revision = "0039_evaluation"
down_revision = "0038_life_goal_binding"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE evaluation_runs (
        evaluation_id TEXT PRIMARY KEY,
        manifest_sha256 TEXT NOT NULL UNIQUE,
        consent_id TEXT NOT NULL REFERENCES consents(consent_id),
        job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
        manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
        sources_json TEXT NOT NULL CHECK(json_valid(sources_json)),
        binding_json TEXT NOT NULL CHECK(json_valid(binding_json)),
        redacted INTEGER NOT NULL DEFAULT 0 CHECK(redacted IN (0,1)),
        created_at TEXT NOT NULL
    )""")
    op.execute("""CREATE TABLE evaluation_results (
        request_id TEXT PRIMARY KEY REFERENCES model_usage(request_id),
        evaluation_id TEXT NOT NULL REFERENCES evaluation_runs(evaluation_id),
        ordinal INTEGER NOT NULL CHECK(ordinal>=0 AND ordinal<6),
        text TEXT NOT NULL,
        text_redacted INTEGER NOT NULL CHECK(text_redacted IN (0,1)),
        finish_reason TEXT,
        evidence_type TEXT NOT NULL CHECK(evidence_type IN
            ('MODEL_RESPONSE','SYNTHETIC_MODEL_RESPONSE')),
        created_at TEXT NOT NULL,
        UNIQUE(evaluation_id,ordinal)
    )""")


def downgrade():
    op.drop_table("evaluation_results")
    op.drop_table("evaluation_runs")
