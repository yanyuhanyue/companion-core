"""Map unadmitted social plans to the original leased jobs, without another queue."""

from alembic import op

revision = "0036_social_plan_jobs"
down_revision = "0035_token_measurement"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE social_autonomy_jobs ("
               "plan_id TEXT PRIMARY KEY REFERENCES social_autonomy_plans(plan_id),"
               "job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id))")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM social_autonomy_jobs LIMIT 1").first():
        raise RuntimeError("SOCIAL_JOB_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("DROP TABLE social_autonomy_jobs")
