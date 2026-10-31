"""Retire untracked Autonomy copies; native evidence owns the rebuilt Life view."""

from alembic import op

revision = "0054_life_autonomy_projection"
down_revision = "0053_owner_jargon"


def upgrade():
    # Some checkouts ran the standalone SQL initializer. These are disposable derived
    # copies without erasure provenance, never an accounting or execution authority.
    op.get_bind().exec_driver_sql("PRAGMA secure_delete=ON")
    for table in (
        "portfolio_changes", "reflection_insights", "interest_portfolio",
        "interest_priorities", "exploration_strategies",
    ):
        op.get_bind().exec_driver_sql(f"DROP TABLE IF EXISTS {table}")


def downgrade():
    # 0053's supported schema never included these ad-hoc tables. Recreating them
    # would restore the unsupported store, and erased derived text stays erased.
    pass
