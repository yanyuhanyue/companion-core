"""Reserve every upstream request of a layered source read."""

from alembic import op

revision = "0014_reader_budget"
down_revision = "0013_content"


def upgrade():
    op.get_bind().exec_driver_sql(
        "ALTER TABLE exploration_requests ADD COLUMN reserved_requests INTEGER NOT NULL DEFAULT 1"
    )
    op.get_bind().exec_driver_sql(
        "ALTER TABLE exploration_requests ADD COLUMN used_requests INTEGER"
    )


def downgrade():
    if (
        op.get_bind()
        .exec_driver_sql("SELECT 1 FROM exploration_requests WHERE reserved_requests<>1 LIMIT 1")
        .first()
    ):
        raise RuntimeError("READER_BUDGET_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    op.drop_column("exploration_requests", "used_requests")
    op.drop_column("exploration_requests", "reserved_requests")
