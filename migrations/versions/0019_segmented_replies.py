"""Keep each reply segment in the original Outbox with one immutable group."""

import sqlalchemy as sa
from alembic import op

revision = "0019_segmented_replies"
down_revision = "0018_reflection_goal_lineage"


def upgrade():
    for column in (
        "reply_group_id TEXT REFERENCES jobs(job_id)",
        "segment_index INTEGER NOT NULL DEFAULT 0 CHECK(segment_index BETWEEN 0 AND 2)",
        "segment_count INTEGER NOT NULL DEFAULT 1 CHECK(segment_count BETWEEN 1 AND 3)",
        "reply_group_sha256 TEXT",
        "segment_interval_ms INTEGER NOT NULL DEFAULT 0 "
        "CHECK(segment_interval_ms BETWEEN 0 AND 3000)",
    ):
        op.get_bind().exec_driver_sql("ALTER TABLE outbox ADD COLUMN " + column)
    op.get_bind().exec_driver_sql(
        "CREATE UNIQUE INDEX idx_outbox_reply_segment ON outbox(reply_group_id,segment_index) "
        "WHERE reply_group_id IS NOT NULL"
    )


def downgrade():
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM outbox WHERE reply_group_id IS NOT NULL LIMIT 1"
    ).first():
        raise RuntimeError("SEGMENTED_REPLIES_DOWNGRADE_REQUIRES_EXPLICIT_REVIEW")
    op.get_bind().exec_driver_sql("DROP INDEX idx_outbox_reply_segment")
    columns = {"segment_interval_ms", "reply_group_sha256", "segment_count", "segment_index",
               "reply_group_id"}
    # Later table rebuilds may reflect the original inline checks/FK as table
    # constraints. Drop those together with their columns, preserving other data.
    table = sa.Table("outbox", sa.MetaData(), autoload_with=op.get_bind())
    for constraint in list(table.constraints):
        if ((isinstance(constraint, sa.CheckConstraint)
             and any(name in str(constraint.sqltext) for name in columns))
            or (isinstance(constraint, sa.ForeignKeyConstraint)
                and any(column.name in columns for column in constraint.columns))):
            table.constraints.remove(constraint)
    with op.batch_alter_table("outbox", copy_from=table, recreate="always") as batch:
        for name in sorted(columns):
            batch.drop_column(name)
