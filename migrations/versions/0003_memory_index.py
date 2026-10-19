"""Derived local semantic index, invalidated by correction and erasure."""

from alembic import op

revision = "0003_memory_index"
down_revision = "0002_m1"


def upgrade():
    op.get_bind().exec_driver_sql(
        "CREATE TABLE memory_vectors ("
        "memory_id TEXT PRIMARY KEY REFERENCES memories(memory_id) ON DELETE CASCADE,"
        "model_version TEXT NOT NULL,content_hash TEXT NOT NULL,vector BLOB NOT NULL)"
    )
    op.get_bind().exec_driver_sql(
        "CREATE TRIGGER memory_vector_invalidate AFTER UPDATE ON memories "
        "WHEN new.content<>old.content OR new.status NOT IN ('CONFIRMED','HYPOTHESIS') "
        "OR new.confidence<=0 BEGIN "
        "DELETE FROM memory_vectors WHERE memory_id=new.memory_id; END"
    )


def downgrade():
    op.get_bind().exec_driver_sql("DROP TRIGGER memory_vector_invalidate")
    op.drop_table("memory_vectors")
