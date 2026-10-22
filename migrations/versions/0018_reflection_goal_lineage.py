"""Attribute reflection-derived goals without guessing the origins of older goals."""

from alembic import op

revision = "0018_reflection_goal_lineage"
down_revision = "0017_paid_model_gate"


def upgrade():
    op.get_bind().exec_driver_sql(
        "ALTER TABLE goals ADD COLUMN reflection_suggestion_id TEXT "
        "REFERENCES reflection_suggestions(suggestion_id)"
    )
    op.get_bind().exec_driver_sql(
        "CREATE INDEX idx_goals_reflection ON goals(reflection_suggestion_id)"
    )


def downgrade():
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM goals WHERE reflection_suggestion_id IS NOT NULL LIMIT 1"
    ).first():
        raise RuntimeError("REFLECTION_GOALS_DOWNGRADE_REQUIRES_EXPLICIT_REVIEW")
    op.get_bind().exec_driver_sql("DROP INDEX idx_goals_reflection")
    op.get_bind().exec_driver_sql("ALTER TABLE goals DROP COLUMN reflection_suggestion_id")
