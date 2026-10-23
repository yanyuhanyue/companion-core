"""Reuse owner content tasks for bounded private file attachments."""

import sqlalchemy as sa
from alembic import op

revision = "0028_private_files"
down_revision = "0027_owner_console"
branch_labels = None
depends_on = None


def _kind_constraint(include_files):
    table = sa.Table("content_items", sa.MetaData(), autoload_with=op.get_bind())
    checks = [constraint for constraint in table.constraints
              if isinstance(constraint, sa.CheckConstraint) and "kind" in str(constraint.sqltext)]
    if len(checks) != 1:
        raise RuntimeError("CONTENT_KIND_CONSTRAINT_UNEXPECTED")
    table.constraints.remove(checks[0])
    kinds = "'link','qq_image','link_image'" + (",'qq_file'" if include_files else "")
    table.append_constraint(sa.CheckConstraint("kind IN (" + kinds + ")",
                                              name="ck_content_kind"))
    return table


def upgrade():
    with op.batch_alter_table("content_items", copy_from=_kind_constraint(True),
                             recreate="always") as batch:
        batch.add_column(sa.Column("attachment_json", sa.Text(), nullable=False,
                                   server_default="{}"))


def downgrade():
    occupied = op.get_bind().exec_driver_sql(
        "SELECT 1 FROM content_items WHERE kind='qq_file' LIMIT 1",
    ).first()
    if occupied:
        raise RuntimeError("PRIVATE_FILES_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    with op.batch_alter_table("content_items", copy_from=_kind_constraint(False),
                             recreate="always") as batch:
        batch.drop_column("attachment_json")
