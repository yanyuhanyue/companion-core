"""Keep existing content records while allowing two articles of up to 32 images."""

import sqlalchemy as sa
from alembic import op

revision = "0015_article_images"
down_revision = "0014_reader_budget"


def change_slot_limit(limit):
    table = sa.Table("content_items", sa.MetaData(), autoload_with=op.get_bind())
    checks = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint) and "slot" in str(constraint.sqltext)
    ]
    if len(checks) != 1:
        raise RuntimeError("CONTENT_SLOT_CONSTRAINT_UNEXPECTED")
    table.constraints.remove(checks[0])
    table.append_constraint(sa.CheckConstraint(f"slot>=0 AND slot<{limit}", name="ck_content_slot"))
    with op.batch_alter_table("content_items", copy_from=table, recreate="always"):
        pass


def upgrade():
    change_slot_limit(64)


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM content_items WHERE slot>=4 LIMIT 1").first():
        raise RuntimeError("ARTICLE_IMAGES_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    change_slot_limit(4)
