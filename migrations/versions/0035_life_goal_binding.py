"""Link an original activity decision to its original native goal."""

from alembic import op

revision = "0035_life_goal_binding"
down_revision = "0034_life_topics"


def upgrade():
    op.get_bind().exec_driver_sql(
        "ALTER TABLE life_activity_results ADD COLUMN goal_id TEXT REFERENCES goals(goal_id)"
    )
    op.get_bind().exec_driver_sql(
        "CREATE INDEX idx_life_activity_goal ON life_activity_results(goal_id)"
    )


def downgrade():
    if (
        op.get_bind()
        .exec_driver_sql("SELECT 1 FROM life_activity_results WHERE goal_id IS NOT NULL LIMIT 1")
        .first()
    ):
        raise RuntimeError("LIFE_GOAL_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    op.drop_index("idx_life_activity_goal", table_name="life_activity_results")
    op.drop_column("life_activity_results", "goal_id")
