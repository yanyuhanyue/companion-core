"""Retain request measurement provenance without rewriting historical admissions."""

from alembic import op

revision = "0035_token_measurement"
down_revision = "0034_voice_retention_v2"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite ADD COLUMN preserves existing rowids, accounting and signed snapshots.
    # An empty object denotes legacy evidence, never an exact tokenizer count.
    op.execute(
        "ALTER TABLE model_admissions ADD COLUMN measurement_json TEXT NOT NULL "
        "DEFAULT '{}' CHECK(json_valid(measurement_json) "
        "AND json_type(measurement_json)='object')"
    )


def downgrade():
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM model_admissions WHERE measurement_json<>'{}' LIMIT 1"
    ).first():
        raise RuntimeError("TOKEN_MEASUREMENT_DOWNGRADE_REQUIRES_EVIDENCE_REVIEW")
    op.execute("ALTER TABLE model_admissions DROP COLUMN measurement_json")
