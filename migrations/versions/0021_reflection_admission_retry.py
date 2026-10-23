"""Persist unadmitted reflection waits without replaying an admitted model request."""

import sqlalchemy as sa
from alembic import op

revision = "0021_reflection_admission_retry"
down_revision = "0020_reply_segment_capacity"


def replace_state_check(*, legacy=False):
    table = sa.Table("reflection_runs", sa.MetaData(), autoload_with=op.get_bind())
    checks = [constraint for constraint in table.constraints
              if isinstance(constraint, sa.CheckConstraint) and "state" in str(constraint.sqltext)]
    if len(checks) != 1:
        raise RuntimeError("REFLECTION_STATE_CONSTRAINT_UNEXPECTED")
    table.constraints.remove(checks[0])
    states = "'RUNNING','COMMITTED','SKIPPED','FAILED','CANCELLED'"
    if not legacy:
        states += ",'RETRY_WAIT'"
    table.append_constraint(sa.CheckConstraint(f"state IN ({states})",
                                              name="ck_reflection_state"))
    with op.batch_alter_table("reflection_runs", copy_from=table, recreate="always") as batch:
        if legacy:
            batch.drop_column("next_attempt_at")
        else:
            batch.add_column(sa.Column("next_attempt_at", sa.Text(), nullable=True))


def upgrade():
    replace_state_check()


def downgrade():
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM reflection_runs WHERE state='RETRY_WAIT' LIMIT 1"
    ).first():
        raise RuntimeError("REFLECTION_RETRY_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    replace_state_check(legacy=True)
