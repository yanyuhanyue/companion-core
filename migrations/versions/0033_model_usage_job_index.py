"""Index native job usage without changing reservations or historical accounting."""

from alembic import op

revision = "0033_model_usage_job_index"
down_revision = "0032_bili_video"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("idx_model_usage_job", "model_usage", ["job_id"])


def downgrade():
    op.drop_index("idx_model_usage_job", table_name="model_usage")
