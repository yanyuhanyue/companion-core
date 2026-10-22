"""Remove the fixed three-fragment ceiling while retaining immutable group constraints."""

import sqlalchemy as sa
from alembic import op

revision = "0020_reply_segment_capacity"
down_revision = "0019_segmented_replies"


def replace_checks(*, legacy=False):
    table = sa.Table("outbox", sa.MetaData(), autoload_with=op.get_bind())
    checks = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
        and any(name in str(constraint.sqltext) for name in ("segment_index", "segment_count"))
    ]
    if len(checks) != 2:
        raise RuntimeError("OUTBOX_SEGMENT_CONSTRAINT_UNEXPECTED")
    for check in checks:
        table.constraints.remove(check)
    if legacy:
        # 0019 drops columns with SQLite ALTER TABLE. Restore its original inline
        # checks, including the interval check that reflection hoists to the table.
        for constraint in list(table.constraints):
            if isinstance(constraint, sa.CheckConstraint) and "segment_interval_ms" in str(
                constraint.sqltext
            ):
                table.constraints.remove(constraint)
        for name, maximum in (
            ("segment_index", 2),
            ("segment_count", 3),
            ("segment_interval_ms", 3000),
        ):
            table.c[name].constraints.add(
                sa.CheckConstraint(
                    f"{name} BETWEEN {1 if name == 'segment_count' else 0} AND {maximum}"
                )
            )
    else:
        table.append_constraint(
            sa.CheckConstraint(
                "segment_index>=0 AND segment_index<segment_count", name="ck_outbox_segment_index"
            )
        )
        table.append_constraint(
            sa.CheckConstraint(
                "segment_count BETWEEN 1 AND 2147483647", name="ck_outbox_segment_count"
            )
        )
    with op.batch_alter_table("outbox", copy_from=table, recreate="always"):
        pass


def upgrade():
    replace_checks()


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM outbox WHERE segment_count>3 LIMIT 1").first():
        raise RuntimeError("REPLY_CAPACITY_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    replace_checks(legacy=True)
